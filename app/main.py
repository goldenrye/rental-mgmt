import asyncio
from contextlib import suppress
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import selectinload

from app.billing import apply_payment, generate_bills_for_lease, month_window
from app.config import get_settings
from app.database import Base, SessionLocal, database_url, engine
from app.deps import CurrentUser, DbSession, Landlord
from app.models import (
    LandlordProfile,
    Lease,
    LeaseTenant,
    Payment,
    RentalBill,
    RentalProperty,
    RentalUnit,
    TenantProfile,
    User,
    UserRole,
)
from app.schemas import (
    BillRead,
    LeaseCreate,
    LeaseRead,
    LeaseUpdate,
    LoginRequest,
    PaymentCreate,
    PaymentRead,
    PaymentResult,
    PropertyCreate,
    PropertyRead,
    PropertyUpdate,
    TenantCreate,
    TenantRead,
    TenantUpdate,
    Token,
    UnitCreate,
    UnitRead,
    UnitUpdate,
    UserCreate,
    UserRead,
)
from app.security import create_access_token, hash_password, verify_password


Base.metadata.create_all(bind=engine)
with engine.begin() as connection:
    columns = {column["name"] for column in inspect(connection).get_columns("leases")}
    if "unit_id" not in columns:
        connection.execute(text("ALTER TABLE leases ADD COLUMN unit_id INTEGER"))
    lease_tenant_columns = {column["name"] for column in inspect(connection).get_columns("lease_tenants")}
    if "monthly_rent" not in lease_tenant_columns:
        connection.execute(text("ALTER TABLE lease_tenants ADD COLUMN monthly_rent NUMERIC(12, 2) NOT NULL DEFAULT 0"))
    if "deposit" not in lease_tenant_columns:
        connection.execute(text("ALTER TABLE lease_tenants ADD COLUMN deposit NUMERIC(12, 2) NOT NULL DEFAULT 0"))
    connection.execute(
        text(
            """
            UPDATE lease_tenants
            SET monthly_rent = (
                SELECT ROUND(leases.monthly_rent / tenant_counts.tenant_count, 2)
                FROM leases
                JOIN (
                    SELECT lease_id, COUNT(*) AS tenant_count
                    FROM lease_tenants
                    GROUP BY lease_id
                ) AS tenant_counts ON tenant_counts.lease_id = leases.id
                WHERE leases.id = lease_tenants.lease_id
            )
            WHERE monthly_rent = 0
              AND lease_id IN (
                SELECT leases.id
                FROM leases
                JOIN (
                    SELECT lease_id, COUNT(*) AS tenant_count
                    FROM lease_tenants
                    GROUP BY lease_id
                ) AS tenant_counts ON tenant_counts.lease_id = leases.id
                WHERE tenant_counts.tenant_count > 0
              )
            """
        )
    )

settings = get_settings()
app = FastAPI(title="Rental Management API", version="1.0.0")
monthly_bill_scheduler_task: Optional[asyncio.Task[None]] = None
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


async def monthly_bill_scheduler() -> None:
    last_run: Optional[date] = None
    while True:
        today = date.today()
        if today.day == 1 and last_run != today:
            with SessionLocal() as db:
                bills = generate_monthly_bills(db, today)
                db.commit()
                if bills:
                    print(f"Generated {len(bills)} monthly rental bill(s) for {today:%Y-%m}.")
            last_run = today
        await asyncio.sleep(60 * 60)


@app.on_event("startup")
async def start_monthly_bill_scheduler() -> None:
    global monthly_bill_scheduler_task
    monthly_bill_scheduler_task = asyncio.create_task(monthly_bill_scheduler())


@app.on_event("shutdown")
async def stop_monthly_bill_scheduler() -> None:
    if monthly_bill_scheduler_task is None:
        return
    monthly_bill_scheduler_task.cancel()
    with suppress(asyncio.CancelledError):
        await monthly_bill_scheduler_task


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
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=str(payload.email),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()

    if payload.role == UserRole.landlord.value:
        db.add(LandlordProfile(user_id=user.id))
    else:
        db.add(TenantProfile(user_id=user.id, full_name=user.full_name, email=user.email))

    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
def login(payload: LoginRequest, db: DbSession) -> Token:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return Token(access_token=create_access_token(str(user.id)))


@app.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user


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
        select(RentalUnit).where(RentalUnit.property_id == property_id, RentalUnit.name == payload.name)
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
                RentalUnit.name == updates["name"],
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
    if payload.user_id is not None:
        user = db.get(User, payload.user_id)
        if user is None or user.role != UserRole.tenant.value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id must reference a tenant user")
        existing_profile = db.scalar(select(TenantProfile).where(TenantProfile.user_id == payload.user_id))
        if existing_profile and existing_profile.landlord_id is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant user is already managed")
        if existing_profile:
            existing_profile.landlord_id = landlord.id
            existing_profile.full_name = payload.full_name
            existing_profile.email = str(payload.email) if payload.email else existing_profile.email
            existing_profile.phone = payload.phone
            existing_profile.notes = payload.notes
            db.commit()
            db.refresh(existing_profile)
            return existing_profile

    tenant = TenantProfile(
        user_id=payload.user_id,
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
