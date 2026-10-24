"""Landlord-managed, read-only MCP API keys, independent of login sessions."""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.account_access import ensure_access, utc
from app.deps import DbSession, LandlordUser
from app.models import McpApiKey, User, UserAccess, UserRole
from app.security import verify_password


router = APIRouter(prefix="/me/mcp-api-keys", tags=["MCP API keys"])
KEY_PATTERN = re.compile(r"rmcp_[A-Za-z0-9_-]{43}", re.ASCII)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    current_password: str = Field(min_length=1, max_length=72)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyRead(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: str
    scope: str = "rental:read"


class ApiKeyCreated(ApiKeyRead):
    api_key: str


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("ascii")).hexdigest()


def key_read(key: McpApiKey, version: int) -> ApiKeyRead:
    expired = key.expires_at is not None and utc(key.expires_at) <= datetime.now(timezone.utc)
    status = "revoked" if key.revoked_at or key.token_version != version else "expired" if expired else "active"
    return ApiKeyRead(
        id=key.id, name=key.name, prefix=key.prefix,
        created_at=utc(key.created_at),
        expires_at=utc(key.expires_at) if key.expires_at else None,
        last_used_at=utc(key.last_used_at) if key.last_used_at else None,
        revoked_at=utc(key.revoked_at) if key.revoked_at else None,
        status=status,
    )


@router.get("", response_model=list[ApiKeyRead])
def list_keys(db: DbSession, user: LandlordUser) -> list[ApiKeyRead]:
    state = db.get(UserAccess, user.id)
    version = state.token_version if state else 0
    keys = db.scalars(select(McpApiKey).where(McpApiKey.user_id == user.id)
                      .order_by(McpApiKey.created_at.desc(), McpApiKey.id)).all()
    return [key_read(key, version) for key in keys]


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_key(payload: ApiKeyCreate, response: Response, db: DbSession, user: LandlordUser) -> ApiKeyCreated:
    response.headers["Cache-Control"] = "no-store"
    if not payload.name.strip():
        raise HTTPException(422, "A key name is required")
    if len(payload.current_password.encode("utf-8")) > 72 or not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    if user.landlord_profile is None:
        raise HTTPException(403, "Landlord profile is required")
    state = ensure_access(db, user)
    now = datetime.now(timezone.utc)
    secret = "rmcp_" + secrets.token_urlsafe(32)
    key = McpApiKey(
        id=uuid4().hex, user_id=user.id, name=payload.name.strip(),
        key_hash=key_digest(secret), prefix=secret[:13], token_version=state.token_version,
        created_at=now,
        expires_at=now + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return ApiKeyCreated(**key_read(key, state.token_version).model_dump(), api_key=secret)


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: str, db: DbSession, user: LandlordUser) -> Response:
    key = db.scalar(select(McpApiKey).where(McpApiKey.id == key_id, McpApiKey.user_id == user.id))
    if key is None:
        raise HTTPException(404, "API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=204)


def validate_api_key(db: Session, secret: str) -> tuple[int, int | None]:
    invalid = ValueError("Invalid, expired, or revoked MCP API key")
    if not KEY_PATTERN.fullmatch(secret):
        raise invalid
    key = db.scalar(select(McpApiKey).where(McpApiKey.key_hash == key_digest(secret)))
    now = datetime.now(timezone.utc)
    if key is None or key.revoked_at or (key.expires_at and utc(key.expires_at) <= now):
        raise invalid
    user = db.get(User, key.user_id)
    state = db.get(UserAccess, key.user_id)
    if (
        user is None or user.role != UserRole.landlord.value or user.landlord_profile is None
        or (state and (not state.is_active or state.password_change_required))
        or key.token_version != (state.token_version if state else 0)
    ):
        raise invalid
    user_id = user.id
    expires_at = int(utc(key.expires_at).timestamp()) if key.expires_at else None
    if key.last_used_at is None or utc(key.last_used_at) < now - timedelta(minutes=1):
        db.execute(update(McpApiKey).where(
            McpApiKey.id == key.id, McpApiKey.revoked_at.is_(None),
        ).values(last_used_at=now))
        db.commit()
    return user_id, expires_at
