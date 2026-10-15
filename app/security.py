from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from passlib.context import CryptContext

from app.config import get_settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def validate_jwt_configuration() -> None:
    secret = get_settings().jwt_secret_key
    if len(secret) < 32 or secret in {
        "change-me-in-production", "replace-this-with-a-long-random-secret",
    }:
        raise RuntimeError("Set JWT_SECRET_KEY to a random secret of at least 32 characters on both API and MCP")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, token_version: int = 0, session_required: bool = False) -> str:
    validate_jwt_configuration()
    settings = get_settings()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": subject, "iat": issued_at, "exp": expires_at, "jti": uuid4().hex,
        "ver": token_version, "session_required": session_required,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    validate_jwt_configuration()
    settings = get_settings()
    return jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp"]},
    )
