from datetime import date

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.billing import apply_payment, generate_bills_for_lease
from app.config import get_settings
from app.database import Base, engine
from app.deps import CurrentUser, DbSession, Landlord
from app.models import (
    LandlordProfile,
    Lease,
    LeaseTenant,
    Payment,
    RentalBill,
    RentalProperty,
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
    UserCreate,
    UserRead,
)
from app.security import create_access_token, hash_password, verify_password


Base.metadata.create_all(bind=engine)

settings = get_settings()
app = FastAPI(title="Rental Management API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def serialize_lease(lease: Lease) -> LeaseRead:
    return LeaseRead(
        id=lease.id,
        landlord_id=lease.landlord_id,
        property_id=lease.property_id,
        start_date=lease.start_date,
        end_date=lease.end_date,
        monthly_rent=lease.monthly_rent,
        tenant_ids=[lease_tenant.tenant_id for lease_tenant in lease.tenants],
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


def ensure_lease_dates(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date must be on or after start_date")


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
    get_property_or_404(db, landlord, payload.property_id)
    load_landlord_tenants(db, landlord, payload.tenant_ids)

    lease = Lease(
        landlord_id=landlord.id,
        property_id=payload.property_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        monthly_rent=payload.monthly_rent,
        notes=payload.notes,
    )
    db.add(lease)
    db.flush()
    for tenant_id in payload.tenant_ids:
        db.add(LeaseTenant(lease_id=lease.id, tenant_id=tenant_id))
    db.flush()
    db.refresh(lease, attribute_names=["tenants"])
    if lease.start_date <= date.today() <= lease.end_date:
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

    if "property_id" in updates:
        get_property_or_404(db, landlord, updates["property_id"])
        lease.property_id = updates["property_id"]
    if "start_date" in updates:
        lease.start_date = updates["start_date"]
    if "end_date" in updates:
        lease.end_date = updates["end_date"]
    ensure_lease_dates(lease.start_date, lease.end_date)
    if "monthly_rent" in updates:
        lease.monthly_rent = updates["monthly_rent"]
    if "notes" in updates:
        lease.notes = updates["notes"]
    if "tenant_ids" in updates:
        load_landlord_tenants(db, landlord, updates["tenant_ids"])
        lease.tenants.clear()
        db.flush()
        for tenant_id in updates["tenant_ids"]:
            lease.tenants.append(LeaseTenant(lease_id=lease.id, tenant_id=tenant_id))

    db.flush()
    db.refresh(lease, attribute_names=["tenants"])
    if lease.start_date <= date.today() <= lease.end_date:
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
    leases = list(
        db.scalars(
            select(Lease)
            .options(selectinload(Lease.tenants))
            .where(
                Lease.landlord_id == landlord.id,
                Lease.start_date <= date.today(),
                Lease.end_date >= date.today(),
            )
        )
    )
    bills: list[RentalBill] = []
    for lease in leases:
        bills.extend(generate_bills_for_lease(db, lease))
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
