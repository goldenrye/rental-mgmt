from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.encrypted_models import EncryptedRecord, SensitiveField, install_encryption
from app.encryption import key_table

key_table.to_metadata(Base.metadata)


class UserRole(str, Enum):
    super_admin = "super_admin"
    landlord = "landlord"
    tenant = "tenant"


class BillStatus(str, Enum):
    open = "open"
    partial = "partial"
    paid = "paid"


class PropertyBillStatus(str, Enum):
    unpaid = "unpaid"
    paid = "paid"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    landlord_profile: Mapped[Optional["LandlordProfile"]] = relationship(back_populates="user")
    tenant_profile: Mapped[Optional["TenantProfile"]] = relationship(back_populates="user")
    access_state: Mapped[Optional["UserAccess"]] = relationship()

    @property
    def password_change_required(self) -> bool:
        return bool(self.access_state and self.access_state.password_change_required)

    @property
    def is_active(self) -> bool:
        return self.access_state is None or self.access_state.is_active

    @property
    def encryption_enabled(self) -> bool:
        return bool(self.landlord_profile and self.landlord_profile.encryption_enabled)


class UserAccess(Base):
    __tablename__ = "user_access"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_version: Mapped[int] = mapped_column(nullable=False, default=0)
    password_change_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_version: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class McpApiKey(Base):
    """Read-only MCP credentials. The full key is returned once, never stored."""

    __tablename__ = "mcp_api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    token_version: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    """Failed password attempts per login name and client IP, including names with no account."""

    __tablename__ = "login_throttles"

    login_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    failures: Mapped[int] = mapped_column(nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LandlordInvite(Base):
    """Single-use landlord sign-up invitation. Only a SHA-256 hash of the code is stored."""

    __tablename__ = "landlord_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    used_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    used_by: Mapped[Optional["User"]] = relationship(foreign_keys=[used_by_id])


class LandlordProfile(Base):
    __tablename__ = "landlord_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    encryption_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    user: Mapped[User] = relationship(back_populates="landlord_profile")
    properties: Mapped[List["RentalProperty"]] = relationship(
        back_populates="landlord", cascade="all, delete-orphan"
    )
    tenants: Mapped[List["TenantProfile"]] = relationship(back_populates="landlord")
    leases: Mapped[List["Lease"]] = relationship(back_populates="landlord")


class GoogleCalendarConnection(Base):
    __tablename__ = "google_calendar_connections"

    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    calendar_id: Mapped[Optional[str]] = mapped_column(String(1024))
    credentials: Mapped[Optional[str]] = mapped_column(Text)
    oauth_state_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    oauth_browser_hash: Mapped[Optional[str]] = mapped_column(String(64))
    oauth_verifier: Mapped[Optional[str]] = mapped_column(Text)
    oauth_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    oauth_token_version: Mapped[Optional[int]] = mapped_column()
    last_error: Mapped[Optional[str]] = mapped_column(String(255))
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class GoogleCalendarExport(Base):
    """Transactional outbox: random event IDs make retries safe even after a lost response."""

    __tablename__ = "google_calendar_exports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("rental_bills.id", ondelete="CASCADE"), unique=True, nullable=False)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bill: Mapped["RentalBill"] = relationship(back_populates="calendar_exports")


class TenantProfile(EncryptedRecord, Base):
    __tablename__ = "tenant_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), unique=True)
    landlord_id: Mapped[Optional[int]] = mapped_column(ForeignKey("landlord_profiles.id"), index=True)
    full_name = SensitiveField(required=True)
    email = SensitiveField()
    phone = SensitiveField()
    notes = SensitiveField()

    user: Mapped[Optional["User"]] = relationship(back_populates="tenant_profile")
    landlord: Mapped[Optional["LandlordProfile"]] = relationship(back_populates="tenants")
    leases: Mapped[List["LeaseTenant"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    bills: Mapped[List["RentalBill"]] = relationship(back_populates="tenant")


class RentalProperty(EncryptedRecord, Base):
    __tablename__ = "rental_properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    name = SensitiveField(required=True)
    address = SensitiveField(required=True)
    description = SensitiveField()

    landlord: Mapped["LandlordProfile"] = relationship(back_populates="properties")
    units: Mapped[List["RentalUnit"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    leases: Mapped[List["Lease"]] = relationship(back_populates="property")
    property_bills: Mapped[List["PropertyBill"]] = relationship(back_populates="property", cascade="all, delete-orphan")


class RentalUnit(EncryptedRecord, Base):
    __tablename__ = "rental_units"
    __table_args__ = (UniqueConstraint("property_id", "name_index", name="uq_property_unit_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("rental_properties.id"), index=True, nullable=False)
    name_index: Mapped[str] = mapped_column(String(64), nullable=False)
    name = SensitiveField(required=True)
    description = SensitiveField()

    property: Mapped["RentalProperty"] = relationship(back_populates="units")
    leases: Mapped[List["Lease"]] = relationship(back_populates="unit")


class Lease(EncryptedRecord, Base):
    __tablename__ = "leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    property_id: Mapped[int] = mapped_column(ForeignKey("rental_properties.id"), index=True, nullable=False)
    unit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rental_units.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    monthly_rent = SensitiveField(money=True, required=True)
    notes = SensitiveField()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    landlord: Mapped["LandlordProfile"] = relationship(back_populates="leases")
    property: Mapped["RentalProperty"] = relationship(back_populates="leases")
    unit: Mapped[Optional["RentalUnit"]] = relationship(back_populates="leases")
    tenants: Mapped[List["LeaseTenant"]] = relationship(back_populates="lease", cascade="all, delete-orphan")
    bills: Mapped[List["RentalBill"]] = relationship(back_populates="lease", cascade="all, delete-orphan")
    billing_runs: Mapped[List["RentBillingRun"]] = relationship(back_populates="lease", cascade="all, delete-orphan")


class RentBillingRun(Base):
    """Durable completion marker; kept even if generated bills are deleted."""

    __tablename__ = "rent_billing_runs"
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), primary_key=True)
    bill_year: Mapped[int] = mapped_column(primary_key=True)
    bill_month: Mapped[int] = mapped_column(primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease: Mapped["Lease"] = relationship(back_populates="billing_runs")


class LeaseTenant(EncryptedRecord, Base):
    __tablename__ = "lease_tenants"
    __table_args__ = (UniqueConstraint("lease_id", "tenant_id", name="uq_lease_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), index=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant_profiles.id"), index=True, nullable=False)
    monthly_rent = SensitiveField(money=True, default=Decimal("0.00"), required=True)
    deposit = SensitiveField(money=True, default=Decimal("0.00"), required=True)

    lease: Mapped["Lease"] = relationship(back_populates="tenants")
    tenant: Mapped["TenantProfile"] = relationship(back_populates="leases")


class PropertyBill(EncryptedRecord, Base):
    __tablename__ = "property_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    property_id: Mapped[int] = mapped_column(ForeignKey("rental_properties.id"), index=True, nullable=False)
    bill_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title = SensitiveField(required=True)
    recurrence: Mapped[str] = mapped_column(String(32), nullable=False, default="one_time")
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due = SensitiveField(money=True, required=True)
    status: Mapped[PropertyBillStatus] = mapped_column(String(32), nullable=False, default=PropertyBillStatus.unpaid.value)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes = SensitiveField()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    property: Mapped["RentalProperty"] = relationship(back_populates="property_bills")


class PropertyBillSeries(Base):
    __tablename__ = "property_bill_series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), nullable=False)
    anchor: Mapped[date] = mapped_column(Date, nullable=False)
    due_day: Mapped[int] = mapped_column(nullable=False)
    recurrence: Mapped[str] = mapped_column(String(32), nullable=False)
    through: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(default=0, nullable=False)


class PropertyBillSeriesMember(Base):
    __tablename__ = "property_bill_series_members"
    __table_args__ = (UniqueConstraint("series_id", "scheduled_date", name="uq_property_series_date"),)

    bill_id: Mapped[int] = mapped_column(ForeignKey("property_bills.id", ondelete="CASCADE"), primary_key=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("property_bill_series.id"), index=True, nullable=False)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)


class RentalBill(EncryptedRecord, Base):
    __tablename__ = "rental_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), index=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant_profiles.id"), index=True, nullable=False)
    source_property_bill_id: Mapped[Optional[int]] = mapped_column(ForeignKey("property_bills.id"), index=True)
    title = SensitiveField(default="Monthly rent", required=True)
    bill_type: Mapped[str] = mapped_column(String(32), nullable=False, default="rent")
    recurrence: Mapped[Optional[str]] = mapped_column(String(32))
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    bill_year: Mapped[int] = mapped_column(nullable=False)
    bill_month: Mapped[int] = mapped_column(nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due = SensitiveField(money=True, required=True)
    amount_paid = SensitiveField(money=True, default=Decimal("0.00"), required=True)
    balance = SensitiveField(money=True, required=True)
    status: Mapped[BillStatus] = mapped_column(String(32), nullable=False, default=BillStatus.open.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lease: Mapped["Lease"] = relationship(back_populates="bills")
    tenant: Mapped["TenantProfile"] = relationship(back_populates="bills")
    payments: Mapped[List["Payment"]] = relationship(back_populates="bill", cascade="all, delete-orphan")
    calendar_exports: Mapped[List["GoogleCalendarExport"]] = relationship(back_populates="bill", cascade="all, delete-orphan")

    @property
    def tenant_name(self) -> str:
        return self.tenant.full_name

    @property
    def is_tenant_charge(self) -> bool:
        return self.source_property_bill_id is not None


class Payment(EncryptedRecord, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("rental_bills.id"), index=True, nullable=False)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant_profiles.id"), index=True, nullable=False)
    amount = SensitiveField(money=True, required=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note = SensitiveField()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bill: Mapped["RentalBill"] = relationship(back_populates="payments")


ENCRYPTED_MODELS = (TenantProfile, RentalProperty, RentalUnit, Lease, LeaseTenant, PropertyBill, RentalBill, Payment)
for encrypted_model in ENCRYPTED_MODELS:
    install_encryption(encrypted_model)
