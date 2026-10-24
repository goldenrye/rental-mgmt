from datetime import date, datetime
from decimal import Decimal
import json
from os import getenv
import sys
from typing import Any, Optional

import jwt
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.authentication import AuthCredentials

from app.database import SessionLocal, engine
from app.encryption_migration import initialize_encrypted_database
from app.models import (
    LandlordProfile,
    Lease,
    LeaseTenant,
    Payment,
    PropertyBill,
    RentalBill,
    RentalProperty,
    TenantProfile,
    User,
    UserRole,
)
from app.security import decode_access_token, validate_jwt_configuration
from app.account_access import validate_account_access
from app.mcp_api_keys import validate_api_key


MCP_TRANSPORT = getenv("RENTAL_MGMT_MCP_TRANSPORT", "stdio").strip()
MCP_HOST = getenv("RENTAL_MGMT_MCP_HOST", "127.0.0.1").strip()
MCP_PORT = int(getenv("RENTAL_MGMT_MCP_PORT", "8001"))
MCP_STREAMABLE_HTTP_PATH = getenv("RENTAL_MGMT_MCP_STREAMABLE_HTTP_PATH", "/mcp").strip()
MCP_SSE_PATH = getenv("RENTAL_MGMT_MCP_SSE_PATH", "/sse").strip()
MCP_SSL_CERTFILE = getenv("RENTAL_MGMT_MCP_SSL_CERTFILE", "").strip()
MCP_SSL_KEYFILE = getenv("RENTAL_MGMT_MCP_SSL_KEYFILE", "").strip()
MCP_AUTH_ENABLED = getenv("RENTAL_MGMT_MCP_AUTH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
MCP_PUBLIC_BASE_URL = getenv("RENTAL_MGMT_MCP_PUBLIC_BASE_URL", f"http://{MCP_HOST}:{MCP_PORT}").strip().rstrip("/")
MCP_AUTH_SERVER_URL = getenv("RENTAL_MGMT_MCP_AUTH_SERVER_URL", "http://localhost:8000").strip().rstrip("/")
VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}

mcp = FastMCP(
    "rental-management",
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path=MCP_STREAMABLE_HTTP_PATH,
    sse_path=MCP_SSE_PATH,
)


def init_database() -> None:
    """Reject missing keys and unmigrated schemas before serving MCP requests."""
    initialize_encrypted_database(engine)


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


def resolve_landlord_id(db: Session, landlord_id: Optional[int] = None, landlord_email: Optional[str] = None) -> int:
    # Read the current message's request, not a process-global or session-cached identity.
    # HTTP/SSE dispatch can outlive the middleware task that accepted the message.
    try:
        request = mcp.get_context().request_context.request
    except (ValueError, LookupError):
        request = None
    if request is not None:
        token = McpOAuthMiddleware.extract_bearer_token(request.scope)
    elif MCP_TRANSPORT == "stdio":
        token = getenv("RENTAL_MGMT_MCP_API_KEY", "").strip() or getenv("RENTAL_MGMT_MCP_TOKEN", "").strip()
    else:
        token = None
    if not token:
        raise ValueError("A landlord MCP API key or access token is required")
    identity = validate_mcp_bearer_token(token)
    user = db.get(User, int(identity.client_id))
    if user is None or user.landlord_profile is None:
        raise ValueError("Landlord profile is required")
    owned_id = user.landlord_profile.id
    if (landlord_id is not None and landlord_id != owned_id) or (
        landlord_email is not None and landlord_email != user.email
    ):
        raise ValueError("Access to another landlord's data is forbidden")
    return owned_id


@mcp.tool()
def database_status():
    """Check the authenticated landlord's database access without exposing connection credentials."""
    with SessionLocal() as db:
        return {"status": "ok", "landlord_id": resolve_landlord_id(db)}


@mcp.tool()
def list_landlords():
    """Return only the authenticated landlord's profile."""
    with SessionLocal() as db:
        owned_id = resolve_landlord_id(db)
        landlords = db.scalars(
            select(LandlordProfile).options(selectinload(LandlordProfile.user))
            .where(LandlordProfile.id == owned_id)
        ).all()
        return [landlord_to_dict(landlord) for landlord in landlords]


@mcp.tool()
def list_properties(landlord_id: int = None, landlord_email: str = None):
    """List the authenticated landlord's properties and units. Landlord filters must match the caller."""
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(RentalProperty).options(selectinload(RentalProperty.units))
        query = query.where(RentalProperty.landlord_id == resolved_landlord_id)
        return [property_to_dict(property_) for property_ in db.scalars(query).all()]


@mcp.tool()
def list_tenants(landlord_id: int = None, landlord_email: str = None):
    """List tenants, including total lease rent share and deposit amount."""
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(TenantProfile).options(selectinload(TenantProfile.leases))
        query = query.where(TenantProfile.landlord_id == resolved_landlord_id)
        return [tenant_to_dict(tenant) for tenant in db.scalars(query).all()]


@mcp.tool()
def list_leases(
    landlord_id: int = None,
    landlord_email: str = None,
    property_id: int = None,
    tenant_id: int = None,
):
    """List leases with property, unit, tenant rent share, and deposit details."""
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(Lease).options(
            selectinload(Lease.property),
            selectinload(Lease.unit),
            selectinload(Lease.tenants).selectinload(LeaseTenant.tenant),
        )
        query = query.where(Lease.landlord_id == resolved_landlord_id)
        if property_id is not None:
            query = query.where(Lease.property_id == property_id)
        if tenant_id is not None:
            query = query.join(Lease.tenants).where(LeaseTenant.tenant_id == tenant_id)
        return [lease_to_dict(lease) for lease in db.scalars(query).unique().all()]


@mcp.tool()
def list_property_bills(
    landlord_id: int = None,
    landlord_email: str = None,
    property_id: int = None,
    status: str = None,
    bill_type: str = None,
    due_from: str = None,
    due_to: str = None,
):
    """List landlord property bills, optionally filtered by property, status, type, and due-date range."""
    start_date = parse_date(due_from, "due_from")
    end_date = parse_date(due_to, "due_to")
    with SessionLocal() as db:
        resolved_landlord_id = resolve_landlord_id(db, landlord_id, landlord_email)
        query = select(PropertyBill).options(selectinload(PropertyBill.property))
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
    landlord_id: int = None,
    landlord_email: str = None,
    tenant_id: int = None,
    property_id: int = None,
    status: str = None,
    due_from: str = None,
    due_to: str = None,
):
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
    landlord_id: int = None,
    landlord_email: str = None,
    tenant_id: int = None,
    paid_from: str = None,
    paid_to: str = None,
):
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
def get_tenant_payment_history(tenant_id: int):
    """Return one tenant's profile, rental bills, and payment history."""
    with SessionLocal() as db:
        owned_id = resolve_landlord_id(db)
        tenant = db.scalar(select(TenantProfile).where(
            TenantProfile.id == tenant_id, TenantProfile.landlord_id == owned_id,
        ))
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")

        bills = db.scalars(
            select(RentalBill)
            .options(
                selectinload(RentalBill.tenant),
                selectinload(RentalBill.lease).selectinload(Lease.property),
                selectinload(RentalBill.lease).selectinload(Lease.unit),
            )
            .where(RentalBill.tenant_id == tenant_id, RentalBill.landlord_id == owned_id)
            .order_by(RentalBill.due_date.desc(), RentalBill.id.desc())
        ).all()
        payments = db.scalars(
            select(Payment)
            .options(
                selectinload(Payment.bill).selectinload(RentalBill.tenant),
                selectinload(Payment.bill).selectinload(RentalBill.lease).selectinload(Lease.property),
                selectinload(Payment.bill).selectinload(RentalBill.lease).selectinload(Lease.unit),
            )
            .where(Payment.tenant_id == tenant_id, Payment.landlord_id == owned_id)
            .order_by(Payment.paid_at.desc(), Payment.id.desc())
        ).all()

        return {
            "tenant": tenant_to_dict(tenant),
            "rental_bills": [rental_bill_to_dict(bill) for bill in bills],
            "payments": [payment_to_dict(payment) for payment in payments],
        }


@mcp.tool()
def get_property_overview(property_id: int):
    """Return one property's units, leases, property bills, and tenant-payable rental bills."""
    with SessionLocal() as db:
        owned_id = resolve_landlord_id(db)
        property_ = db.scalar(
            select(RentalProperty)
            .options(selectinload(RentalProperty.units))
            .where(RentalProperty.id == property_id, RentalProperty.landlord_id == owned_id)
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
            .where(Lease.property_id == property_id, Lease.landlord_id == owned_id)
            .order_by(Lease.start_date.desc(), Lease.id.desc())
        ).all()
        property_bills = db.scalars(
            select(PropertyBill)
            .options(selectinload(PropertyBill.property))
            .where(PropertyBill.property_id == property_id, PropertyBill.landlord_id == owned_id)
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
            .where(Lease.property_id == property_id, RentalBill.landlord_id == owned_id)
            .order_by(RentalBill.due_date.desc(), RentalBill.id.desc())
        ).unique().all()

        return {
            "property": property_to_dict(property_),
            "leases": [lease_to_dict(lease) for lease in leases],
            "property_bills": [property_bill_to_dict(bill) for bill in property_bills],
            "rental_bills": [rental_bill_to_dict(bill) for bill in rental_bills],
        }


class EnsureMcpAcceptHeader:
    """Fill in the Accept header the streamable-http transport requires.

    Some clients (e.g. Claude Desktop connectors) omit or narrow the Accept header,
    which the MCP SDK rejects with 406 Not Acceptable.
    """

    REQUIRED = ("application/json", "text/event-stream")

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope["headers"] if k != b"accept"]
            accept = b", ".join(v for k, v in scope["headers"] if k == b"accept").decode("latin-1")
            missing = [t for t in self.REQUIRED if t not in accept and "*/*" not in accept]
            if missing or "*/*" in accept:
                accept = ", ".join(filter(None, [accept, *self.REQUIRED]))
            headers.append((b"accept", accept.encode("latin-1")))
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


class McpOAuthMiddleware:
    """Protect remote MCP transports with API keys or OAuth bearer tokens."""

    PUBLIC_PATHS = {
        "/health",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
    }

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if path == "/health":
            await self.send_json(send, 200, {"status": "ok"})
            return
        if path in {
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        }:
            await self.send_json(send, 200, protected_resource_metadata())
            return
        if path == "/.well-known/oauth-authorization-server":
            await self.send_json(send, 200, authorization_server_metadata())
            return

        token = self.extract_bearer_token(scope)
        if token is None:
            await self.send_unauthorized(send, "missing_token", "A single MCP API key or Authorization bearer token is required")
            return
        try:
            identity = validate_mcp_bearer_token(token)
        except ValueError as exc:
            await self.send_unauthorized(send, "invalid_token", str(exc))
            return

        # Let the SDK bind both SSE and streamable HTTP sessions to their owner.
        scope = {**scope, "user": AuthenticatedUser(identity), "auth": AuthCredentials(identity.scopes)}
        await self.app(scope, receive, send)

    @staticmethod
    def extract_bearer_token(scope: Any) -> Optional[str]:
        credentials = [(key.lower(), value) for key, value in scope.get("headers", [])
                       if key.lower() in {b"authorization", b"x-api-key"}]
        # Reject ambiguous credentials rather than authenticating different identities
        # in transport middleware and individual tool dispatch.
        if len(credentials) != 1:
            return None
        key, value = credentials[0]
        credential = value.decode("latin-1").strip()
        if key == b"x-api-key":
            return credential if credential.startswith("rmcp_") else None
        scheme, _, token = credential.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return None

    @staticmethod
    async def send_json(send: Any, status_code: int, payload: dict[str, Any], headers: Optional[list[tuple[bytes, bytes]]] = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if headers:
            response_headers.extend(headers)
        await send({"type": "http.response.start", "status": status_code, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def send_unauthorized(self, send: Any, error: str, description: str) -> None:
        authenticate = (
            'Bearer realm="rental-management-mcp", '
            f'error="{error}", '
            f'error_description="{description}", '
            f'resource_metadata="{MCP_PUBLIC_BASE_URL}/.well-known/oauth-protected-resource/mcp"'
        )
        await self.send_json(
            send,
            401,
            {"error": error, "error_description": description},
            headers=[(b"www-authenticate", authenticate.encode("latin-1"))],
        )


def protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": MCP_PUBLIC_BASE_URL,
        "authorization_servers": [MCP_AUTH_SERVER_URL],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["rental:read"],
    }


def authorization_server_metadata() -> dict[str, Any]:
    return {
        "issuer": MCP_AUTH_SERVER_URL,
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["rental:read"],
        "response_types_supported": ["token"],
        "grant_types_supported": ["password"],
        "token_endpoint": f"{MCP_AUTH_SERVER_URL}/oauth/token",
    }


def validate_mcp_bearer_token(token: str) -> AccessToken:
    if token.startswith("rmcp_"):
        with SessionLocal() as db:
            user_id, expires_at = validate_api_key(db, token)
            return AccessToken(
                token=token, client_id=str(user_id), subject=str(user_id),
                scopes=["rental:read"], expires_at=expires_at,
            )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired JWT access token") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdigit():
        raise ValueError("JWT subject must identify a user")
    try:
        user_id = int(subject)
    except ValueError as exc:
        raise ValueError("Invalid JWT subject") from exc
    if not 0 < user_id <= 9223372036854775807:
        raise ValueError("Invalid JWT subject")

    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise ValueError("JWT user no longer exists")
        validate_account_access(db, user, payload)
        if user.role != UserRole.landlord.value:
            raise ValueError("Landlord access is required for MCP")
        if user.landlord_profile is None:
            raise ValueError("Landlord profile is required for MCP")
        return AccessToken(
            token=token, client_id=str(user.id), subject=str(user.id),
            scopes=["rental:read"], expires_at=int(payload["exp"]),
        )


def secure_remote_mcp_app(app: Any) -> Any:
    if not MCP_AUTH_ENABLED:
        raise ValueError("Remote MCP authentication cannot be disabled")
    return McpOAuthMiddleware(app)


if __name__ == "__main__":
    validate_jwt_configuration()
    if MCP_TRANSPORT not in VALID_TRANSPORTS:
        raise ValueError(
            "RENTAL_MGMT_MCP_TRANSPORT must be one of: "
            f"{', '.join(sorted(VALID_TRANSPORTS))}"
        )
    if bool(MCP_SSL_CERTFILE) != bool(MCP_SSL_KEYFILE):
        raise ValueError(
            "RENTAL_MGMT_MCP_SSL_CERTFILE and RENTAL_MGMT_MCP_SSL_KEYFILE must be set together"
        )
    use_https = bool(MCP_SSL_CERTFILE) and MCP_TRANSPORT != "stdio"
    init_database()
    print(
        "Starting Rental Management MCP server "
        f"with transport={MCP_TRANSPORT}, host={MCP_HOST}, port={MCP_PORT}, "
        f"https={use_https}, auth_enabled={MCP_AUTH_ENABLED}, auth_mode=api-key-or-oauth-jwt",
        file=sys.stderr,
    )
    if MCP_TRANSPORT == "stdio":
        mcp.run(transport=MCP_TRANSPORT)
    else:
        import uvicorn

        app = mcp.sse_app() if MCP_TRANSPORT == "sse" else EnsureMcpAcceptHeader(mcp.streamable_http_app())
        app = secure_remote_mcp_app(app)
        ssl_options = (
            {"ssl_certfile": MCP_SSL_CERTFILE, "ssl_keyfile": MCP_SSL_KEYFILE} if use_https else {}
        )
        uvicorn.run(
            app,
            host=MCP_HOST,
            port=MCP_PORT,
            log_level=mcp.settings.log_level.lower(),
            **ssl_options,
        )
