from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(str, Enum):
    landlord = "landlord"
    tenant = "tenant"


class BillStatus(str, Enum):
    open = "open"
    partial = "partial"
    paid = "paid"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    landlord_profile: Mapped[Optional["LandlordProfile"]] = relationship(back_populates="user")
    tenant_profile: Mapped[Optional["TenantProfile"]] = relationship(back_populates="user")


class LandlordProfile(Base):
    __tablename__ = "landlord_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="landlord_profile")
    properties: Mapped[List["RentalProperty"]] = relationship(
        back_populates="landlord", cascade="all, delete-orphan"
    )
    tenants: Mapped[List["TenantProfile"]] = relationship(back_populates="landlord")
    leases: Mapped[List["Lease"]] = relationship(back_populates="landlord")


class TenantProfile(Base):
    __tablename__ = "tenant_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), unique=True)
    landlord_id: Mapped[Optional[int]] = mapped_column(ForeignKey("landlord_profiles.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped[Optional["User"]] = relationship(back_populates="tenant_profile")
    landlord: Mapped[Optional["LandlordProfile"]] = relationship(back_populates="tenants")
    leases: Mapped[List["LeaseTenant"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    bills: Mapped[List["RentalBill"]] = relationship(back_populates="tenant")


class RentalProperty(Base):
    __tablename__ = "rental_properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    landlord: Mapped["LandlordProfile"] = relationship(back_populates="properties")
    leases: Mapped[List["Lease"]] = relationship(back_populates="property")


class Lease(Base):
    __tablename__ = "leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    property_id: Mapped[int] = mapped_column(ForeignKey("rental_properties.id"), index=True, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    monthly_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    landlord: Mapped["LandlordProfile"] = relationship(back_populates="leases")
    property: Mapped["RentalProperty"] = relationship(back_populates="leases")
    tenants: Mapped[List["LeaseTenant"]] = relationship(back_populates="lease", cascade="all, delete-orphan")
    bills: Mapped[List["RentalBill"]] = relationship(back_populates="lease", cascade="all, delete-orphan")


class LeaseTenant(Base):
    __tablename__ = "lease_tenants"
    __table_args__ = (UniqueConstraint("lease_id", "tenant_id", name="uq_lease_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), index=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant_profiles.id"), index=True, nullable=False)

    lease: Mapped["Lease"] = relationship(back_populates="tenants")
    tenant: Mapped["TenantProfile"] = relationship(back_populates="leases")


class RentalBill(Base):
    __tablename__ = "rental_bills"
    __table_args__ = (UniqueConstraint("lease_id", "tenant_id", "bill_year", "bill_month", name="uq_bill_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), index=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant_profiles.id"), index=True, nullable=False)
    bill_year: Mapped[int] = mapped_column(nullable=False)
    bill_month: Mapped[int] = mapped_column(nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[BillStatus] = mapped_column(String(32), nullable=False, default=BillStatus.open.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lease: Mapped["Lease"] = relationship(back_populates="bills")
    tenant: Mapped["TenantProfile"] = relationship(back_populates="bills")
    payments: Mapped[List["Payment"]] = relationship(back_populates="bill", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("rental_bills.id"), index=True, nullable=False)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlord_profiles.id"), index=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant_profiles.id"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bill: Mapped["RentalBill"] = relationship(back_populates="payments")
