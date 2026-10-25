import asyncio
from contextlib import suppress
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.billing import apply_payment, generate_bills_for_lease, iter_months, money, month_window, monthly_due_date
from app.rent_scheduler import monthly_bill_scheduler
from app.config import get_settings
from app.database import SessionLocal, database_url, engine
from app.encryption import EncryptionError, unit_name_index
from app.encryption_preferences import set_encryption_preference
from app.schemas import EncryptionPreferenceUpdate
from app.encryption_migration import initialize_encrypted_database
from app.deps import BearerToken, CurrentUser, DbSession, Landlord
from app.models import (
    LandlordProfile,
    Lease,
    LeaseTenant,
    Payment,
    PropertyBill,
    RentalBill,
    RentalProperty,
    RentalUnit,
    TenantProfile,
    User,
    UserRole,
)
from app.schemas import (
    MAX_BILL_PERIOD_YEARS,
    BillRead,
    GenerateTenantCharges,
    LeaseCreate,
    LeaseRead,
    LeaseUpdate,
    LoginRequest,
    ManualBillCreate,
    PasswordChangeRequest,
    PaymentCreate,
    PaymentRead,
    PaymentResult,
    ProfileUpdate,
    PropertyBillCreate,
    PropertyBillRead,
    PropertyBillUpdate,
    PropertyCreate,
    PropertyRead,
    PropertyUpdate,
    TenantCreate,
    TenantChargePreview,
    TenantRead,
    TenantUpdate,
    Token,
    UnitCreate,
    UnitRead,
    UnitUpdate,
    UserCreate,
    UserRead,
)
from app.security import decode_access_token, hash_password, verify_password
from app.account_access import bootstrap_super_admin, ensure_access, issue_login, revoke_all_sessions, revoke_session
from app.admin import router as admin_router
from app.mcp_api_keys import router as mcp_api_keys_router
from app.google_calendar import router as google_calendar_router, calendar_export_scheduler
from app.invites import claim_invite, find_usable_invite
from app.login_throttle import authenticate, client_ip
from app.tenant_charges import preview_tenant_charges
from app.bill_extensions import confirm_extension, preview_extension, track_created_series
from app.schemas import ConfirmPropertyBillExtension, ExtendPropertyBillRequest, PropertyBillExtensionPreview


initialize_encrypted_database(engine)
with engine.begin() as connection:
    user_columns = {column["name"] for column in inspect(connection).get_columns("users")}
    if "display_name" not in user_columns:
        connection.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"))

settings = get_settings()
with SessionLocal() as db:
    bootstrap_super_admin(db)
app = FastAPI(title="Rental Management API", version="1.0.0")
app.include_router(admin_router)
app.include_router(mcp_api_keys_router)
app.include_router(google_calendar_router)


@app.exception_handler(StaleDataError)
async def encrypted_record_conflict(request: Request, exc: StaleDataError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "This record changed in another request. Reload it and try again."})


monthly_bill_scheduler_task: Optional[asyncio.Task[None]] = None
calendar_export_scheduler_task: Optional[asyncio.Task[None]] = None
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def log_database_location() -> None:
    print(f"Rental Management database: {database_url}")


def load_billable_leases(db: DbSession, billing_date: date, landlord_id: Optional[int] = None) -> list[Lease]:
    period_start, period_end = month_window(billing_date)
    query = (
        select(Lease)
        .options(selectinload(Lease.tenants))
        .where(
            Lease.start_date <= period_end,
            Lease.end_date >= period_start,
        )
    )
    if landlord_id is not None:
        query = query.where(Lease.landlord_id == landlord_id)
    return list(db.scalars(query))


def generate_monthly_bills(db: DbSession, billing_date: Optional[date] = None, landlord_id: Optional[int] = None) -> list[RentalBill]:
    target_date = billing_date or date.today()
    bills: list[RentalBill] = []
    for lease in load_billable_leases(db, target_date, landlord_id):
        bills.extend(generate_bills_for_lease(db, lease, target_date))
    return bills


def iter_annual_dates(start: date, end: date) -> list[date]:
    cursor = start
    dates: list[date] = []
    while cursor <= end:
        dates.append(cursor)
        next_year = cursor.year + 1
        try:
            cursor = cursor.replace(year=next_year)
        except ValueError:
            cursor = cursor.replace(year=next_year, day=28)
    return dates


# Monthly bills over the maximum period; also caps annual bills whose due date precedes the period.
MAX_BILL_OCCURRENCES = 12 * MAX_BILL_PERIOD_YEARS + 1


def manual_bill_due_dates(payload: ManualBillCreate | PropertyBillCreate) -> list[date]:
    due_dates = _manual_bill_due_dates(payload)
    if len(due_dates) > MAX_BILL_OCCURRENCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A recurring bill can create at most {MAX_BILL_OCCURRENCES} occurrences",
        )
    return due_dates


def _manual_bill_due_dates(payload: ManualBillCreate | PropertyBillCreate) -> list[date]:
    if payload.recurrence in {"one_time", "custom_period"}:
        return [payload.due_date]
    if payload.period_start is None or payload.period_end is None:
        return [payload.due_date]
    month_steps = {"monthly": 1, "bi_monthly": 2, "quarterly": 3, "semi_annually": 6}
    if payload.recurrence in month_steps:
        month_step = month_steps[payload.recurrence]
        return [
            monthly_due_date(year, month, payload.due_date.day)
            for year, month in iter_months(payload.period_start, payload.period_end)[::month_step]
        ]
    if payload.recurrence == "annually":
        return iter_annual_dates(payload.due_date, payload.period_end)
    return [payload.due_date]


@app.on_event("startup")
async def start_monthly_bill_scheduler() -> None:
    global monthly_bill_scheduler_task, calendar_export_scheduler_task
    monthly_bill_scheduler_task = asyncio.create_task(monthly_bill_scheduler())
    calendar_export_scheduler_task = asyncio.create_task(calendar_export_scheduler())


@app.on_event("shutdown")
async def stop_monthly_bill_scheduler() -> None:
    for task in (monthly_bill_scheduler_task, calendar_export_scheduler_task):
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def serialize_lease(lease: Lease) -> LeaseRead:
    return LeaseRead(
        id=lease.id,
        landlord_id=lease.landlord_id,
        property_id=lease.property_id,
        unit_id=lease.unit_id,
        start_date=lease.start_date,
        end_date=lease.end_date,
        monthly_rent=lease.monthly_rent,
        tenant_ids=[lease_tenant.tenant_id for lease_tenant in lease.tenants],
        tenants=[
            {
                "tenant_id": lease_tenant.tenant_id,
                "monthly_rent": lease_tenant.monthly_rent,
                "deposit": lease_tenant.deposit,
            }
            for lease_tenant in lease.tenants
        ],
        notes=lease.notes,
    )


def get_property_or_404(db: DbSession, landlord: LandlordProfile, property_id: int) -> RentalProperty:
    rental_property = db.scalar(
        select(RentalProperty).where(
            RentalProperty.id == property_id,
            RentalProperty.landlord_id == landlord.id,
        )
    )
    if rental_property is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return rental_property


def get_tenant_or_404(db: DbSession, landlord: LandlordProfile, tenant_id: int) -> TenantProfile:
    tenant = db.scalar(
        select(TenantProfile).where(
            TenantProfile.id == tenant_id,
            TenantProfile.landlord_id == landlord.id,
        )
    )
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def get_unit_or_404(db: DbSession, landlord: LandlordProfile, unit_id: int) -> RentalUnit:
    unit = db.scalar(
        select(RentalUnit)
        .join(RentalProperty, RentalUnit.property_id == RentalProperty.id)
        .where(
            RentalUnit.id == unit_id,
            RentalProperty.landlord_id == landlord.id,
        )
    )
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found")
    return unit


def get_lease_or_404(db: DbSession, landlord: LandlordProfile, lease_id: int) -> Lease:
    lease = db.scalar(
        select(Lease)
        .options(selectinload(Lease.tenants))
        .where(
            Lease.id == lease_id,
            Lease.landlord_id == landlord.id,
        )
    )
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lease not found")
    return lease


def get_bill_or_404(db: DbSession, landlord: LandlordProfile, bill_id: int) -> RentalBill:
    bill = db.scalar(
        select(RentalBill).where(
            RentalBill.id == bill_id,
            RentalBill.landlord_id == landlord.id,
        )
    )
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill


def get_property_bill_or_404(db: DbSession, landlord: LandlordProfile, bill_id: int) -> PropertyBill:
    bill = db.scalar(
        select(PropertyBill).where(
            PropertyBill.id == bill_id,
            PropertyBill.landlord_id == landlord.id,
        )
    )
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property bill not found")
    return bill


def assert_unique_tenant_ids(tenant_ids: list[int]) -> None:
    if len(tenant_ids) != len(set(tenant_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tenant_ids must be unique")


def load_landlord_tenants(db: DbSession, landlord: LandlordProfile, tenant_ids: list[int]) -> list[TenantProfile]:
    assert_unique_tenant_ids(tenant_ids)
    tenants = list(
        db.scalars(
            select(TenantProfile).where(
                TenantProfile.landlord_id == landlord.id,
                TenantProfile.id.in_(tenant_ids),
            )
        )
    )
    if len(tenants) != len(tenant_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more tenants were not found")
    return tenants


def tenant_term_value(tenant_term, field: str):
    if isinstance(tenant_term, dict):
        return tenant_term[field]
    return getattr(tenant_term, field)


def validate_lease_tenant_terms(db: DbSession, landlord: LandlordProfile, tenant_terms: list) -> None:
    load_landlord_tenants(db, landlord, [tenant_term_value(tenant, "tenant_id") for tenant in tenant_terms])


def ensure_tenant_rents_match_monthly_rent(tenant_terms: list, monthly_rent) -> None:
    rent_total = sum((tenant_term_value(tenant, "monthly_rent") for tenant in tenant_terms), start=Decimal("0.00"))
    if rent_total != monthly_rent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant monthly rents must add up to lease monthly rent",
        )


def ensure_lease_dates(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date must be on or after start_date")


def validate_lease_unit(db: DbSession, landlord: LandlordProfile, property_id: int, unit_id: Optional[int]) -> None:
    get_property_or_404(db, landlord, property_id)
    if unit_id is None:
        return
    unit = get_unit_or_404(db, landlord, unit_id)
    if unit.property_id != property_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unit must belong to the selected property")


def assert_no_overlapping_lease(
    db: DbSession,
    landlord: LandlordProfile,
    property_id: int,
    unit_id: Optional[int],
    start_date: date,
    end_date: date,
    exclude_lease_id: Optional[int] = None,
) -> None:
    query = select(Lease).where(
        Lease.landlord_id == landlord.id,
        Lease.property_id == property_id,
        Lease.start_date <= end_date,
        Lease.end_date >= start_date,
    )
    if exclude_lease_id is not None:
        query = query.where(Lease.id != exclude_lease_id)
    if unit_id is not None:
        query = query.where(or_(Lease.unit_id == unit_id, Lease.unit_id.is_(None)))

    overlapping_lease = db.scalar(query.limit(1))
    if overlapping_lease is None:
        return

    target = "this property" if unit_id is None else "this property and unit"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"There is another lease already associated with {target} "
            f"that overlaps this lease period: lease #{overlapping_lease.id} "
            f"({overlapping_lease.start_date} to {overlapping_lease.end_date})."
        ),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession) -> User:
    invite = None
    if payload.invite_code:
        if payload.role != UserRole.landlord.value:
            raise HTTPException(status_code=400, detail="Invitations are for landlord accounts")
        try:
            invite = find_usable_invite(db, payload.invite_code, str(payload.email))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif not settings.open_registration:
        if payload.role == UserRole.landlord.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Landlord sign-up is by invitation only. Ask the administrator for an invitation link.",
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant self-registration is disabled")
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=str(payload.email),
        full_name=payload.full_name,
        display_name=payload.display_name or None,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()

    if payload.role == UserRole.landlord.value:
        db.add(LandlordProfile(user_id=user.id))
        if invite is not None:
            try:
                claim_invite(db, invite, user)
            except ValueError as exc:
                db.rollback()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        db.add(TenantProfile(user_id=user.id, full_name=user.full_name, email=user.email))

    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
def login(payload: LoginRequest, db: DbSession, request: Request) -> Token:
    user = authenticate(db, str(payload.email), payload.password, client_ip(request))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    try:
        return Token(access_token=issue_login(db, user, "web"))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/oauth/token", response_model=Token)
def oauth_token(
    db: DbSession,
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
) -> Token:
    user = authenticate(db, form.username, form.password, client_ip(request))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    if user.role != UserRole.landlord.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Landlord access is required")
    try:
        return Token(access_token=issue_login(db, user, "oauth"))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/auth/logout", status_code=204)
def logout(db: DbSession, user: CurrentUser, token: BearerToken) -> Response:
    revoke_session(db, user, decode_access_token(token))
    return Response(status_code=204)


@app.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user


@app.put("/me/encryption", response_model=UserRead)
def update_encryption_preference(payload: EncryptionPreferenceUpdate, db: DbSession, user: CurrentUser, landlord: Landlord) -> User:
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    try:
        set_encryption_preference(db, landlord.id, payload.enabled)
        db.commit()
    except EncryptionError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Storage conversion is unavailable. Ask the administrator to check the encryption key and data integrity. Your setting was not changed.") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    return user


@app.patch("/me", response_model=UserRead)
def update_profile(payload: ProfileUpdate, db: DbSession, user: CurrentUser) -> User:
    user.full_name = payload.full_name
    user.display_name = payload.display_name or None

    if payload.new_password:
        if not payload.current_password or not verify_password(payload.current_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
        if payload.new_password == payload.current_password:
            raise HTTPException(status_code=400, detail="Choose a different password")
        user.hashed_password = hash_password(payload.new_password)
        ensure_access(db, user).password_change_required = False
        revoke_all_sessions(db, user)

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChangeRequest, db: DbSession, user: CurrentUser) -> Response:
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="Choose a different password")
    user.hashed_password = hash_password(payload.new_password)
    ensure_access(db, user).password_change_required = False
    revoke_all_sessions(db, user)
    db.add(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/properties", response_model=PropertyRead, status_code=status.HTTP_201_CREATED)
def create_property(payload: PropertyCreate, db: DbSession, landlord: Landlord) -> RentalProperty:
    rental_property = RentalProperty(landlord_id=landlord.id, **payload.model_dump())
    db.add(rental_property)
    db.commit()
    db.refresh(rental_property)
    return rental_property


@app.get("/properties", response_model=list[PropertyRead])
def list_properties(db: DbSession, landlord: Landlord) -> list[RentalProperty]:
    return list(db.scalars(select(RentalProperty).where(RentalProperty.landlord_id == landlord.id)))


@app.get("/properties/{property_id}", response_model=PropertyRead)
def get_property(property_id: int, db: DbSession, landlord: Landlord) -> RentalProperty:
    return get_property_or_404(db, landlord, property_id)


@app.patch("/properties/{property_id}", response_model=PropertyRead)
def update_property(
    property_id: int, payload: PropertyUpdate, db: DbSession, landlord: Landlord
) -> RentalProperty:
    rental_property = get_property_or_404(db, landlord, property_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rental_property, field, value)
    db.commit()
    db.refresh(rental_property)
    return rental_property


@app.delete("/properties/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_property(property_id: int, db: DbSession, landlord: Landlord) -> Response:
    rental_property = get_property_or_404(db, landlord, property_id)
    active_lease = db.scalar(select(Lease.id).where(Lease.property_id == rental_property.id))
    if active_lease:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Property has leases and cannot be deleted")
    db.delete(rental_property)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/properties/{property_id}/units", response_model=UnitRead, status_code=status.HTTP_201_CREATED)
def create_unit(property_id: int, payload: UnitCreate, db: DbSession, landlord: Landlord) -> RentalUnit:
    get_property_or_404(db, landlord, property_id)
    duplicate = db.scalar(
        select(RentalUnit).where(
            RentalUnit.property_id == property_id,
            RentalUnit.name_index == unit_name_index(db.connection(), f"landlord:{landlord.id}", property_id, payload.name),
        )
    )
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unit name already exists for this property")
    unit = RentalUnit(property_id=property_id, **payload.model_dump())
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


@app.get("/properties/{property_id}/units", response_model=list[UnitRead])
def list_property_units(property_id: int, db: DbSession, landlord: Landlord) -> list[RentalUnit]:
    get_property_or_404(db, landlord, property_id)
    return list(db.scalars(select(RentalUnit).where(RentalUnit.property_id == property_id)))


@app.get("/units", response_model=list[UnitRead])
def list_units(db: DbSession, landlord: Landlord) -> list[RentalUnit]:
    return list(
        db.scalars(
            select(RentalUnit)
            .join(RentalProperty, RentalUnit.property_id == RentalProperty.id)
            .where(RentalProperty.landlord_id == landlord.id)
        )
    )


@app.patch("/units/{unit_id}", response_model=UnitRead)
def update_unit(unit_id: int, payload: UnitUpdate, db: DbSession, landlord: Landlord) -> RentalUnit:
    unit = get_unit_or_404(db, landlord, unit_id)
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        duplicate = db.scalar(
            select(RentalUnit).where(
                RentalUnit.property_id == unit.property_id,
                RentalUnit.name_index == unit_name_index(db.connection(), f"landlord:{landlord.id}", unit.property_id, updates["name"]),
                RentalUnit.id != unit.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unit name already exists for this property")
    for field, value in updates.items():
        setattr(unit, field, value)
    db.commit()
    db.refresh(unit)
    return unit


@app.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_unit(unit_id: int, db: DbSession, landlord: Landlord) -> Response:
    unit = get_unit_or_404(db, landlord, unit_id)
    linked_lease = db.scalar(select(Lease.id).where(Lease.unit_id == unit.id))
    if linked_lease:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unit has leases and cannot be deleted")
    db.delete(unit)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/tenants", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: TenantCreate, db: DbSession, landlord: Landlord) -> TenantProfile:
    tenant = TenantProfile(
        landlord_id=landlord.id,
        full_name=payload.full_name,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        notes=payload.notes,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@app.get("/tenants", response_model=list[TenantRead])
def list_tenants(db: DbSession, landlord: Landlord) -> list[TenantProfile]:
    return list(db.scalars(select(TenantProfile).where(TenantProfile.landlord_id == landlord.id)))


@app.get("/tenants/{tenant_id}", response_model=TenantRead)
def get_tenant(tenant_id: int, db: DbSession, landlord: Landlord) -> TenantProfile:
    return get_tenant_or_404(db, landlord, tenant_id)


@app.patch("/tenants/{tenant_id}", response_model=TenantRead)
def update_tenant(tenant_id: int, payload: TenantUpdate, db: DbSession, landlord: Landlord) -> TenantProfile:
    tenant = get_tenant_or_404(db, landlord, tenant_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(tenant, field, str(value) if field == "email" and value else value)
    db.commit()
    db.refresh(tenant)
    return tenant


@app.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(tenant_id: int, db: DbSession, landlord: Landlord) -> Response:
    tenant = get_tenant_or_404(db, landlord, tenant_id)
    linked_lease = db.scalar(select(LeaseTenant.id).where(LeaseTenant.tenant_id == tenant.id))
    linked_bill = db.scalar(select(RentalBill.id).where(RentalBill.tenant_id == tenant.id))
    if linked_lease or linked_bill:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant has leases or bills and cannot be deleted")
    db.delete(tenant)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/leases", response_model=LeaseRead, status_code=status.HTTP_201_CREATED)
def create_lease(payload: LeaseCreate, db: DbSession, landlord: Landlord) -> LeaseRead:
    validate_lease_unit(db, landlord, payload.property_id, payload.unit_id)
    assert_no_overlapping_lease(db, landlord, payload.property_id, payload.unit_id, payload.start_date, payload.end_date)
    validate_lease_tenant_terms(db, landlord, payload.tenants)

    lease = Lease(
        landlord_id=landlord.id,
        property_id=payload.property_id,
        unit_id=payload.unit_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        monthly_rent=payload.monthly_rent,
        notes=payload.notes,
    )
    db.add(lease)
    db.flush()
    for tenant in payload.tenants:
        db.add(
            LeaseTenant(
                lease_id=lease.id,
                tenant_id=tenant_term_value(tenant, "tenant_id"),
                monthly_rent=tenant_term_value(tenant, "monthly_rent"),
                deposit=tenant_term_value(tenant, "deposit"),
            )
        )
    db.flush()
    db.refresh(lease, attribute_names=["tenants"])
    generate_bills_for_lease(db, lease)
    db.commit()
    db.refresh(lease, attribute_names=["tenants"])
    return serialize_lease(lease)


@app.get("/leases", response_model=list[LeaseRead])
def list_leases(db: DbSession, landlord: Landlord) -> list[LeaseRead]:
    leases = list(
        db.scalars(
            select(Lease).options(selectinload(Lease.tenants)).where(Lease.landlord_id == landlord.id)
        )
    )
    return [serialize_lease(lease) for lease in leases]


@app.get("/leases/{lease_id}", response_model=LeaseRead)
def get_lease(lease_id: int, db: DbSession, landlord: Landlord) -> LeaseRead:
    return serialize_lease(get_lease_or_404(db, landlord, lease_id))


@app.patch("/leases/{lease_id}", response_model=LeaseRead)
def update_lease(lease_id: int, payload: LeaseUpdate, db: DbSession, landlord: Landlord) -> LeaseRead:
    lease = get_lease_or_404(db, landlord, lease_id)
    updates = payload.model_dump(exclude_unset=True)

    next_property_id = updates.get("property_id", lease.property_id)
    next_unit_id = updates.get("unit_id", lease.unit_id)
    if "property_id" in updates or "unit_id" in updates:
        validate_lease_unit(db, landlord, next_property_id, next_unit_id)
        lease.property_id = next_property_id
        lease.unit_id = next_unit_id
    if "start_date" in updates:
        lease.start_date = updates["start_date"]
    if "end_date" in updates:
        lease.end_date = updates["end_date"]
    ensure_lease_dates(lease.start_date, lease.end_date)
    assert_no_overlapping_lease(
        db,
        landlord,
        lease.property_id,
        lease.unit_id,
        lease.start_date,
        lease.end_date,
        exclude_lease_id=lease.id,
    )
    next_monthly_rent = updates.get("monthly_rent", lease.monthly_rent)
    next_tenants = updates.get("tenants")
    if next_tenants is not None:
        validate_lease_tenant_terms(db, landlord, next_tenants)
        ensure_tenant_rents_match_monthly_rent(next_tenants, next_monthly_rent)
    elif "monthly_rent" in updates:
        ensure_tenant_rents_match_monthly_rent(lease.tenants, next_monthly_rent)
    if "monthly_rent" in updates:
        lease.monthly_rent = next_monthly_rent
    if "notes" in updates:
        lease.notes = updates["notes"]
    if next_tenants is not None:
        lease.tenants.clear()
        db.flush()
        for tenant in next_tenants:
            lease.tenants.append(
                LeaseTenant(
                    lease_id=lease.id,
                    tenant_id=tenant_term_value(tenant, "tenant_id"),
                    monthly_rent=tenant_term_value(tenant, "monthly_rent"),
                    deposit=tenant_term_value(tenant, "deposit"),
                )
            )

    db.flush()
    db.refresh(lease, attribute_names=["tenants"])
    generate_bills_for_lease(db, lease)
    db.commit()
    db.refresh(lease, attribute_names=["tenants"])
    return serialize_lease(lease)


@app.delete("/leases/{lease_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lease(lease_id: int, db: DbSession, landlord: Landlord) -> Response:
    lease = get_lease_or_404(db, landlord, lease_id)
    if lease.bills:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Lease has bills and cannot be deleted")
    db.delete(lease)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/leases/{lease_id}/generate-bills", response_model=list[BillRead])
def generate_lease_bills(lease_id: int, db: DbSession, landlord: Landlord) -> list[RentalBill]:
    lease = get_lease_or_404(db, landlord, lease_id)
    bills = generate_bills_for_lease(db, lease)
    db.commit()
    for bill in bills:
        db.refresh(bill)
    return bills


@app.post("/bills/generate-current-month", response_model=list[BillRead])
def generate_current_month_bills(db: DbSession, landlord: Landlord) -> list[RentalBill]:
    bills = generate_monthly_bills(db, landlord_id=landlord.id)
    db.commit()
    for bill in bills:
        db.refresh(bill)
    return bills


@app.get("/property-bills", response_model=list[PropertyBillRead])
def list_property_bills(db: DbSession, landlord: Landlord) -> list[PropertyBill]:
    return list(db.scalars(select(PropertyBill).where(PropertyBill.landlord_id == landlord.id)))


@app.post("/property-bills", response_model=list[PropertyBillRead], status_code=status.HTTP_201_CREATED)
def create_property_bills(payload: PropertyBillCreate, db: DbSession, landlord: Landlord) -> list[PropertyBill]:
    get_property_or_404(db, landlord, payload.property_id)
    bills: list[PropertyBill] = []
    for due_date in manual_bill_due_dates(payload):
        bill = PropertyBill(
            landlord_id=landlord.id,
            property_id=payload.property_id,
            bill_type=payload.bill_type,
            title=payload.title,
            recurrence=payload.recurrence,
            period_start=payload.period_start,
            period_end=payload.period_end,
            due_date=due_date,
            amount_due=money(payload.amount_due),
            status="unpaid",
            notes=payload.notes,
        )
        db.add(bill)
        bills.append(bill)
    track_created_series(db, payload, bills, landlord.id)
    db.commit()
    for bill in bills:
        db.refresh(bill)
    return bills


@app.post("/property-bills/{bill_id}/extension-preview", response_model=PropertyBillExtensionPreview)
def preview_property_bill_extension(
    bill_id: int, payload: ExtendPropertyBillRequest, db: DbSession, landlord: Landlord,
) -> PropertyBillExtensionPreview:
    bill = get_property_bill_or_404(db, landlord, bill_id)
    return preview_extension(db, bill, payload)[0]


@app.post("/property-bills/{bill_id}/extend", response_model=list[PropertyBillRead], status_code=201)
def extend_property_bill(
    bill_id: int, payload: ConfirmPropertyBillExtension, db: DbSession, landlord: Landlord,
) -> list[PropertyBill]:
    bill = get_property_bill_or_404(db, landlord, bill_id)
    return confirm_extension(db, bill, payload)


@app.patch("/property-bills/{bill_id}", response_model=PropertyBillRead)
def update_property_bill(
    bill_id: int, payload: PropertyBillUpdate, db: DbSession, landlord: Landlord
) -> PropertyBill:
    bill = get_property_bill_or_404(db, landlord, bill_id)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("status") == "paid" and updates.get("paid_at") is None and bill.paid_at is None:
        updates["paid_at"] = datetime.now(timezone.utc)
    if updates.get("status") == "unpaid":
        updates["paid_at"] = None
    for field, value in updates.items():
        setattr(bill, field, value)
    db.commit()
    db.refresh(bill)
    return bill


@app.delete("/property-bills/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_property_bill(bill_id: int, db: DbSession, landlord: Landlord) -> Response:
    bill = get_property_bill_or_404(db, landlord, bill_id)
    db.delete(bill)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/property-bills/{bill_id}/tenant-bills-preview", response_model=TenantChargePreview)
def preview_tenant_bills_from_property_bill(bill_id: int, db: DbSession, landlord: Landlord) -> TenantChargePreview:
    property_bill = get_property_bill_or_404(db, landlord, bill_id)
    return preview_tenant_charges(db, property_bill)


@app.post("/property-bills/{bill_id}/generate-tenant-bills", response_model=list[BillRead], status_code=status.HTTP_201_CREATED)
def generate_tenant_bills_from_property_bill(
    bill_id: int, payload: GenerateTenantCharges, db: DbSession, landlord: Landlord
) -> list[RentalBill]:
    property_bill = get_property_bill_or_404(db, landlord, bill_id)
    preview = preview_tenant_charges(db, property_bill)
    if payload.preview_token != preview.preview_token:
        raise HTTPException(status_code=409, detail="Bill or tenant details changed. Refresh the preview and confirm again.")
    if not preview.tenants:
        raise HTTPException(status_code=400, detail="No active tenants found for this property bill period.")

    # Atomically claim generation so concurrent confirmations cannot create duplicates.
    claimed = db.execute(
        update(PropertyBill)
        .where(
            PropertyBill.id == bill_id,
            PropertyBill.landlord_id == landlord.id,
            PropertyBill.generated_at.is_(None),
        )
        .values(generated_at=datetime.now(timezone.utc))
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tenant charges have already been generated for this bill.")

    bills: list[RentalBill] = []
    for share in preview.tenants:
        tenant_bill = RentalBill(
            landlord_id=landlord.id,
            lease_id=share.lease_id,
            tenant_id=share.tenant_id,
            source_property_bill_id=property_bill.id,
            title=property_bill.title,
            bill_type=property_bill.bill_type,
            recurrence=property_bill.recurrence,
            period_start=property_bill.period_start,
            period_end=property_bill.period_end,
            bill_year=preview.due_date.year,
            bill_month=preview.due_date.month,
            due_date=preview.due_date,
            amount_due=share.amount_due,
            amount_paid=Decimal("0.00"),
            balance=share.amount_due,
            status="open" if share.amount_due > 0 else "paid",
        )
        db.add(tenant_bill)
        bills.append(tenant_bill)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant charges could not be generated because a conflicting bill already exists.",
        ) from exc
    for bill in bills:
        db.refresh(bill)
    return bills


@app.post("/bills/manual", response_model=list[BillRead], status_code=status.HTTP_201_CREATED)
def create_manual_bills(payload: ManualBillCreate, db: DbSession, landlord: Landlord) -> list[RentalBill]:
    lease = get_lease_or_404(db, landlord, payload.lease_id)
    lease_tenant_ids = {lease_tenant.tenant_id for lease_tenant in lease.tenants}
    requested_tenant_ids = set(payload.tenant_ids)
    if not requested_tenant_ids.issubset(lease_tenant_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected tenants must belong to the selected lease")

    amount_due = money(payload.amount_due)
    due_dates = manual_bill_due_dates(payload)
    bills: list[RentalBill] = []
    for due_date in due_dates:
        for tenant_id in payload.tenant_ids:
            bill = RentalBill(
                landlord_id=landlord.id,
                lease_id=lease.id,
                tenant_id=tenant_id,
                title=payload.title,
                bill_type="manual",
                recurrence=payload.recurrence,
                period_start=payload.period_start,
                period_end=payload.period_end,
                bill_year=due_date.year,
                bill_month=due_date.month,
                due_date=due_date,
                amount_due=amount_due,
                amount_paid=Decimal("0.00"),
                balance=amount_due,
                status="open",
            )
            db.add(bill)
            bills.append(bill)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A bill already exists for the same lease, tenant, and month. "
                "If this happens on an older local database, recreate the database or migrate the old bill uniqueness constraint."
            ),
        ) from exc
    for bill in bills:
        db.refresh(bill)
    return bills


@app.get("/bills", response_model=list[BillRead])
def list_bills(db: DbSession, landlord: Landlord) -> list[RentalBill]:
    return list(db.scalars(select(RentalBill).where(RentalBill.landlord_id == landlord.id)))


@app.get("/bills/{bill_id}", response_model=BillRead)
def get_bill(bill_id: int, db: DbSession, landlord: Landlord) -> RentalBill:
    return get_bill_or_404(db, landlord, bill_id)


@app.delete("/bills/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bill(bill_id: int, db: DbSession, landlord: Landlord) -> Response:
    bill = get_bill_or_404(db, landlord, bill_id)
    db.delete(bill)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/bills/{bill_id}/payments", response_model=PaymentResult, status_code=status.HTTP_201_CREATED)
def record_payment(bill_id: int, payload: PaymentCreate, db: DbSession, landlord: Landlord) -> PaymentResult:
    bill = get_bill_or_404(db, landlord, bill_id)
    payment = apply_payment(db, bill, payload.amount, payload.paid_at, payload.note)
    db.commit()
    db.refresh(payment)
    db.refresh(bill)
    return PaymentResult(payment=PaymentRead.model_validate(payment), bill=BillRead.model_validate(bill))


@app.get("/payments", response_model=list[PaymentRead])
def list_payments(db: DbSession, landlord: Landlord) -> list[Payment]:
    return list(db.scalars(select(Payment).where(Payment.landlord_id == landlord.id)))


@app.get("/tenant/bills", response_model=list[BillRead])
def list_my_tenant_bills(db: DbSession, user: CurrentUser) -> list[RentalBill]:
    if user.role != UserRole.tenant.value or user.tenant_profile is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access required")
    return list(db.scalars(select(RentalBill).where(RentalBill.tenant_id == user.tenant_profile.id)))
