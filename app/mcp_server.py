from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from os import getenv
import sys
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, selectinload

from app.database import Base, SessionLocal, database_url, engine
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
)


MCP_TRANSPORT = getenv("RENTAL_MGMT_MCP_TRANSPORT", "stdio").strip()
MCP_HOST = getenv("RENTAL_MGMT_MCP_HOST", "127.0.0.1").strip()
MCP_PORT = int(getenv("RENTAL_MGMT_MCP_PORT", "8001"))
MCP_STREAMABLE_HTTP_PATH = getenv("RENTAL_MGMT_MCP_STREAMABLE_HTTP_PATH", "/mcp").strip()
MCP_SSE_PATH = getenv("RENTAL_MGMT_MCP_SSE_PATH", "/sse").strip()
VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}

mcp = FastMCP(
    "rental-management",
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path=MCP_STREAMABLE_HTTP_PATH,
    sse_path=MCP_SSE_PATH,
)


def init_database() -> None:
    """Create tables and apply lightweight compatibility migrations for local MCP use."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        lease_columns = {column["name"] for column in inspect(connection).get_columns("leases")}
        if "unit_id" not in lease_columns:
            connection.execute(text("ALTER TABLE leases ADD COLUMN unit_id INTEGER"))

        lease_tenant_columns = {column["name"] for column in inspect(connection).get_columns("lease_tenants")}
        if "monthly_rent" not in lease_tenant_columns:
            connection.execute(text("ALTER TABLE lease_tenants ADD COLUMN monthly_rent NUMERIC(12, 2) NOT NULL DEFAULT 0"))
        if "deposit" not in lease_tenant_columns:
            connection.execute(text("ALTER TABLE lease_tenants ADD COLUMN deposit NUMERIC(12, 2) NOT NULL DEFAULT 0"))

        rental_bill_columns = {column["name"] for column in inspect(connection).get_columns("rental_bills")}
        if "title" not in rental_bill_columns:
            connection.execute(text("ALTER TABLE rental_bills ADD COLUMN title VARCHAR(255) NOT NULL DEFAULT 'Monthly rent'"))
        if "bill_type" not in rental_bill_columns:
            connection.execute(text("ALTER TABLE rental_bills ADD COLUMN bill_type VARCHAR(32) NOT NULL DEFAULT 'rent'"))
        if "recurrence" not in rental_bill_columns:
            connection.execute(text("ALTER TABLE rental_bills ADD COLUMN recurrence VARCHAR(32)"))
        if "period_start" not in rental_bill_columns:
            connection.execute(text("ALTER TABLE rental_bills ADD COLUMN period_start DATE"))
        if "period_end" not in rental_bill_columns:
            connection.execute(text("ALTER TABLE rental_bills ADD COLUMN period_end DATE"))
        if "source_property_bill_id" not in rental_bill_columns:
            connection.execute(text("ALTER TABLE rental_bills ADD COLUMN source_property_bill_id INTEGER"))


def serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def landlord_to_dict(landlord: LandlordProfile) -> dict[str, Any]:
    return {
        "id": landlord.id,
        "user_id": landlord.user_id,
        "email": landlord.user.email if landlord.user else None,
        "full_name": landlord.user.full_name if landlord.user else None,
    }


def property_to_dict(property_: RentalProperty) -> dict[str, Any]:
    return {
        "id": property_.id,
        "landlord_id": property_.landlord_id,
        "name": property_.name,
        "address": property_.address,
        "description": property_.description,
        "units": [
            {"id": unit.id, "name": unit.name, "description": unit.description}
            for unit in property_.units
        ],
    }


def tenant_to_dict(tenant: TenantProfile) -> dict[str, Any]:
    deposit_total = sum((lease_tenant.deposit for lease_tenant in tenant.leases), Decimal("0.00"))
    monthly_rent_total = sum((lease_tenant.monthly_rent for lease_tenant in tenant.leases), Decimal("0.00"))
    return {
        "id": tenant.id,
        "landlord_id": tenant.landlord_id,
        "user_id": tenant.user_id,
        "full_name": tenant.full_name,
        "email": tenant.email,
        "phone": tenant.phone,
        "notes": tenant.notes,
        "lease_count": len(tenant.leases),
        "monthly_rent_total": serialize_value(monthly_rent_total),
        "deposit_total": serialize_value(deposit_total),
    }


def lease_to_dict(lease: Lease) -> dict[str, Any]:
    return {
        "id": lease.id,
        "landlord_id": lease.landlord_id,
        "property_id": lease.property_id,
        "property_name": lease.property.name if lease.property else None,
        "unit_id": lease.unit_id,
        "unit_name": lease.unit.name if lease.unit else None,
        "start_date": serialize_value(lease.start_date),
        "end_date": serialize_value(lease.end_date),
        "monthly_rent": serialize_value(lease.monthly_rent),
        "notes": lease.notes,
        "tenants": [
            {
                "tenant_id": lease_tenant.tenant_id,
                "tenant_name": lease_tenant.tenant.full_name if lease_tenant.tenant else None,
                "monthly_rent": serialize_value(lease_tenant.monthly_rent),
                "deposit": serialize_value(lease_tenant.deposit),
            }
            for lease_tenant in lease.tenants
        ],
    }


def property_bill_to_dict(bill: PropertyBill) -> dict[str, Any]:
    return {
        "id": bill.id,
        "landlord_id": bill.landlord_id,
        "property_id": bill.property_id,
        "property_name": bill.property.name if bill.property else None,
        "bill_type": bill.bill_type,
        "title": bill.title,
        "recurrence": bill.recurrence,
        "period_start": serialize_value(bill.period_start),
        "period_end": serialize_value(bill.period_end),
        "due_date": serialize_value(bill.due_date),
        "amount_due": serialize_value(bill.amount_due),
        "status": bill.status,
        "paid_at": serialize_value(bill.paid_at),
        "generated_at": serialize_value(bill.generated_at),
        "notes": bill.notes,
    }


def rental_bill_to_dict(bill: RentalBill) -> dict[str, Any]:
    return {
        "id": bill.id,
        "landlord_id": bill.landlord_id,
        "lease_id": bill.lease_id,
        "tenant_id": bill.tenant_id,
        "tenant_name": bill.tenant.full_name if bill.tenant else None,
        "property_id": bill.lease.property_id if bill.lease else None,
        "property_name": bill.lease.property.name if bill.lease and bill.lease.property else None,
        "unit_id": bill.lease.unit_id if bill.lease else None,
        "unit_name": bill.lease.unit.name if bill.lease and bill.lease.unit else None,
        "source_property_bill_id": bill.source_property_bill_id,
        "title": bill.title,
        "bill_type": bill.bill_type,
        "recurrence": bill.recurrence,
        "period_start": serialize_value(bill.period_start),
        "period_end": serialize_value(bill.period_end),
        "bill_year": bill.bill_year,
        "bill_month": bill.bill_month,
        "due_date": serialize_value(bill.due_date),
        "amount_due": serialize_value(bill.amount_due),
        "amount_paid": serialize_value(bill.amount_paid),
        "balance": serialize_value(bill.balance),
        "status": bill.status,
    }


def payment_to_dict(payment: Payment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "bill_id": payment.bill_id,
        "landlord_id": payment.landlord_id,
        "tenant_id": payment.tenant_id,
        "tenant_name": payment.bill.tenant.full_name if payment.bill and payment.bill.tenant else None,
        "amount": serialize_value(payment.amount),
        "paid_at": serialize_value(payment.paid_at),
        "note": payment.note,
        "bill": rental_bill_to_dict(payment.bill) if payment.bill else None,
    }


def parse_date(value: Optional[str], field_name: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format") from exc


def resolve_landlord_id(db: Session, landlord_id: Optional[int], landlord_email: Optional[str]) -> Optional[int]:
    if landlord_id is not None:
        return landlord_id
    if landlord_email is None:
        return None

    user = db.scalar(select(User).where(User.email == landlord_email))
    if user is None or user.landlord_profile is None:
        raise ValueError(f"No landlord found for email {landlord_email}")
    return user.landlord_profile.id


@mcp.tool()
def database_status() -> dict[str, Any]:
    """Return the resolved database URL used by the MCP server."""
    return {"database_url": database_url}


@mcp.tool()
def list_landlords() -> list[dict[str, Any]]:
    """List landlord profiles so agents can find a landlord_id for other tools."""
    with SessionLocal() as db:
        landlords = db.scalars(select(LandlordProfile).options(selectinload(LandlordProfile.user))).all()
        return [landlord_to_dict(landlord) for landlord in landlords]


@mcp.tool()
def list_properties(landlord_id: Optional[int] = None, landlord_email: Optional[str] = None) -> list[dict[str, Any]]:
    """List rental properties and units, optionally filtered by landlord_id or landlord_email."""
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(RentalProperty).options(selectinload(RentalProperty.units))
        if resolved_landlord_id is not None:
            query = query.where(RentalProperty.landlord_id == resolved_landlord_id)
        return [property_to_dict(property_) for property_ in db.scalars(query).all()]


@mcp.tool()
def list_tenants(landlord_id: Optional[int] = None, landlord_email: Optional[str] = None) -> list[dict[str, Any]]:
    """List tenants, including total lease rent share and deposit amount."""
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(TenantProfile).options(selectinload(TenantProfile.leases))
        if resolved_landlord_id is not None:
            query = query.where(TenantProfile.landlord_id == resolved_landlord_id)
        return [tenant_to_dict(tenant) for tenant in db.scalars(query).all()]


@mcp.tool()
def list_leases(
    landlord_id: Optional[int] = None,
    landlord_email: Optional[str] = None,
    property_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """List leases with property, unit, tenant rent share, and deposit details."""
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(Lease).options(
            selectinload(Lease.property),
            selectinload(Lease.unit),
            selectinload(Lease.tenants).selectinload(LeaseTenant.tenant),
        )
        if resolved_landlord_id is not None:
            query = query.where(Lease.landlord_id == resolved_landlord_id)
        if property_id is not None:
            query = query.where(Lease.property_id == property_id)
        if tenant_id is not None:
            query = query.join(Lease.tenants).where(LeaseTenant.tenant_id == tenant_id)
        return [lease_to_dict(lease) for lease in db.scalars(query).unique().all()]


@mcp.tool()
def list_property_bills(
    landlord_id: Optional[int] = None,
    landlord_email: Optional[str] = None,
    property_id: Optional[int] = None,
    status: Optional[str] = None,
    bill_type: Optional[str] = None,
    due_from: Optional[str] = None,
    due_to: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List landlord property bills, optionally filtered by property, status, type, and due-date range."""
    start_date = parse_date(due_from, "due_from")
    end_date = parse_date(due_to, "due_to")
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(PropertyBill).options(selectinload(PropertyBill.property))
        if resolved_landlord_id is not None:
            query = query.where(PropertyBill.landlord_id == resolved_landlord_id)
        if property_id is not None:
            query = query.where(PropertyBill.property_id == property_id)
        if status is not None:
            query = query.where(PropertyBill.status == status)
        if bill_type is not None:
            query = query.where(PropertyBill.bill_type == bill_type)
        if start_date is not None:
            query = query.where(PropertyBill.due_date >= start_date)
        if end_date is not None:
            query = query.where(PropertyBill.due_date <= end_date)
        query = query.order_by(PropertyBill.due_date.desc(), PropertyBill.id.desc())
        return [property_bill_to_dict(bill) for bill in db.scalars(query).all()]


@mcp.tool()
def list_rental_bills(
    landlord_id: Optional[int] = None,
    landlord_email: Optional[str] = None,
    tenant_id: Optional[int] = None,
    property_id: Optional[int] = None,
    status: Optional[str] = None,
    due_from: Optional[str] = None,
    due_to: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List tenant-payable rental bills, optionally filtered by tenant, property, status, and due-date range."""
    start_date = parse_date(due_from, "due_from")
    end_date = parse_date(due_to, "due_to")
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(RentalBill).options(
            selectinload(RentalBill.tenant),
            selectinload(RentalBill.lease).selectinload(Lease.property),
            selectinload(RentalBill.lease).selectinload(Lease.unit),
        )
        if resolved_landlord_id is not None:
            query = query.where(RentalBill.landlord_id == resolved_landlord_id)
        if tenant_id is not None:
            query = query.where(RentalBill.tenant_id == tenant_id)
        if property_id is not None:
            query = query.join(RentalBill.lease).where(Lease.property_id == property_id)
        if status is not None:
            query = query.where(RentalBill.status == status)
        if start_date is not None:
            query = query.where(RentalBill.due_date >= start_date)
        if end_date is not None:
            query = query.where(RentalBill.due_date <= end_date)
        query = query.order_by(RentalBill.due_date.desc(), RentalBill.id.desc())
        return [rental_bill_to_dict(bill) for bill in db.scalars(query).unique().all()]


@mcp.tool()
def list_payments(
    landlord_id: Optional[int] = None,
    landlord_email: Optional[str] = None,
    tenant_id: Optional[int] = None,
    paid_from: Optional[str] = None,
    paid_to: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List tenant payment records with related rental bill context."""
    start_date = parse_date(paid_from, "paid_from")
    end_date = parse_date(paid_to, "paid_to")
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(Payment).options(
            selectinload(Payment.bill).selectinload(RentalBill.tenant),
            selectinload(Payment.bill).selectinload(RentalBill.lease).selectinload(Lease.property),
            selectinload(Payment.bill).selectinload(RentalBill.lease).selectinload(Lease.unit),
        )
        if resolved_landlord_id is not None:
            query = query.where(Payment.landlord_id == resolved_landlord_id)
        if tenant_id is not None:
            query = query.where(Payment.tenant_id == tenant_id)
        if start_date is not None:
            query = query.where(Payment.paid_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date is not None:
            query = query.where(Payment.paid_at <= datetime.combine(end_date, datetime.max.time()))
        query = query.order_by(Payment.paid_at.desc(), Payment.id.desc())
        return [payment_to_dict(payment) for payment in db.scalars(query).unique().all()]


@mcp.tool()
def get_tenant_payment_history(tenant_id: int) -> dict[str, Any]:
    """Return one tenant's profile, rental bills, and payment history."""
    with SessionLocal() as db:
        tenant = db.scalar(select(TenantProfile).where(TenantProfile.id == tenant_id))
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")

        bills = db.scalars(
            select(RentalBill)
            .options(
                selectinload(RentalBill.tenant),
                selectinload(RentalBill.lease).selectinload(Lease.property),
                selectinload(RentalBill.lease).selectinload(Lease.unit),
            )
            .where(RentalBill.tenant_id == tenant_id)
            .order_by(RentalBill.due_date.desc(), RentalBill.id.desc())
        ).all()
        payments = db.scalars(
            select(Payment)
            .options(
                selectinload(Payment.bill).selectinload(RentalBill.tenant),
                selectinload(Payment.bill).selectinload(RentalBill.lease).selectinload(Lease.property),
                selectinload(Payment.bill).selectinload(RentalBill.lease).selectinload(Lease.unit),
            )
            .where(Payment.tenant_id == tenant_id)
            .order_by(Payment.paid_at.desc(), Payment.id.desc())
        ).all()

        return {
            "tenant": tenant_to_dict(tenant),
            "rental_bills": [rental_bill_to_dict(bill) for bill in bills],
            "payments": [payment_to_dict(payment) for payment in payments],
        }


@mcp.tool()
def get_property_overview(property_id: int) -> dict[str, Any]:
    """Return one property's units, leases, property bills, and tenant-payable rental bills."""
    with SessionLocal() as db:
        property_ = db.scalar(
            select(RentalProperty)
            .options(selectinload(RentalProperty.units))
            .where(RentalProperty.id == property_id)
        )
        if property_ is None:
            raise ValueError(f"Property {property_id} not found")

        leases = db.scalars(
            select(Lease)
            .options(
                selectinload(Lease.property),
                selectinload(Lease.unit),
                selectinload(Lease.tenants).selectinload(LeaseTenant.tenant),
            )
            .where(Lease.property_id == property_id)
            .order_by(Lease.start_date.desc(), Lease.id.desc())
        ).all()
        property_bills = db.scalars(
            select(PropertyBill)
            .options(selectinload(PropertyBill.property))
            .where(PropertyBill.property_id == property_id)
            .order_by(PropertyBill.due_date.desc(), PropertyBill.id.desc())
        ).all()
        rental_bills = db.scalars(
            select(RentalBill)
            .join(RentalBill.lease)
            .options(
                selectinload(RentalBill.tenant),
                selectinload(RentalBill.lease).selectinload(Lease.property),
                selectinload(RentalBill.lease).selectinload(Lease.unit),
            )
            .where(Lease.property_id == property_id)
            .order_by(RentalBill.due_date.desc(), RentalBill.id.desc())
        ).unique().all()

        return {
            "property": property_to_dict(property_),
            "leases": [lease_to_dict(lease) for lease in leases],
            "property_bills": [property_bill_to_dict(bill) for bill in property_bills],
            "rental_bills": [rental_bill_to_dict(bill) for bill in rental_bills],
        }


if __name__ == "__main__":
    if MCP_TRANSPORT not in VALID_TRANSPORTS:
        raise ValueError(
            "RENTAL_MGMT_MCP_TRANSPORT must be one of: "
            f"{', '.join(sorted(VALID_TRANSPORTS))}"
        )
    init_database()
    print(
        "Starting Rental Management MCP server "
        f"with transport={MCP_TRANSPORT}, host={MCP_HOST}, port={MCP_PORT}",
        file=sys.stderr,
    )
    mcp.run(transport=MCP_TRANSPORT)
