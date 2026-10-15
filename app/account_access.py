"""Shared account status and session checks for API and MCP authentication."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LoginSession, User, UserAccess, UserRole
from app.security import create_access_token, decode_access_token, hash_password


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def ensure_access(db: Session, user: User) -> UserAccess:
    state = db.get(UserAccess, user.id)
    if state is None:
        try:
            with db.begin_nested():
                state = UserAccess(user_id=user.id)
                db.add(state)
                db.flush()
        except IntegrityError:
            state = db.get(UserAccess, user.id)
            if state is None:
                raise
    return state


def bootstrap_super_admin(db: Session) -> None:
    existing = db.scalar(select(User).where(User.email == "admin"))
    if existing:
        if existing.role != UserRole.super_admin.value:
            raise RuntimeError("The reserved admin username belongs to a non-admin account.")
        return
    try:
        with db.begin_nested():
            admin = User(
                email="admin", full_name="Super Admin", role=UserRole.super_admin.value,
                hashed_password=hash_password(get_settings().super_admin_initial_password),
            )
            db.add(admin)
            db.flush()
            db.add(UserAccess(user_id=admin.id, password_change_required=True))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(User).where(User.email == "admin"))
        if existing is None or existing.role != UserRole.super_admin.value:
            raise


def issue_login(db: Session, user: User, source: str) -> str:
    state = ensure_access(db, user)
    if not state.is_active:
        raise ValueError("Account is deactivated. Contact the administrator.")
    now = datetime.now(timezone.utc)
    token = create_access_token(str(user.id), state.token_version, session_required=True)
    claims = decode_access_token(token)
    db.add(LoginSession(
        id=claims["jti"], user_id=user.id, token_version=state.token_version,
        source=source, created_at=now, last_seen_at=now,
        expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
    ))
    state.last_login_at = now
    db.commit()
    return token


def validate_account_access(db: Session, user: User, claims: dict) -> None:
    state = db.get(UserAccess, user.id)
    if state and not state.is_active:
        raise ValueError("Account is deactivated")
    if claims.get("ver", 0) != (state.token_version if state else 0):
        raise ValueError("Session was revoked. Sign in again.")
    session_id = claims.get("jti")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("Invalid session")
    session = db.get(LoginSession, session_id) if session_id else None
    if claims.get("session_required") and session is None:
        raise ValueError("Session no longer exists")
    if session:
        now = datetime.now(timezone.utc)
        if session.user_id != user.id or session.revoked_at or utc(session.expires_at) <= now:
            raise ValueError("Session was revoked or expired")
        # Limit activity writes; no token or password is stored in the session table.
        if utc(session.last_seen_at) < now - timedelta(minutes=1):
            db.execute(update(LoginSession).where(
                LoginSession.id == session.id, LoginSession.revoked_at.is_(None),
            ).values(last_seen_at=now))
            db.commit()


def revoke_all_sessions(db: Session, user: User) -> None:
    ensure_access(db, user)
    db.execute(update(UserAccess).where(UserAccess.user_id == user.id).values(
        token_version=UserAccess.token_version + 1,
    ))
    db.execute(update(LoginSession).where(
        LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(timezone.utc)))


def revoke_session(db: Session, user: User, claims: dict) -> None:
    session = db.get(LoginSession, claims["jti"]) if claims.get("jti") else None
    if session:
        session.revoked_at = datetime.now(timezone.utc)
    else:
        # Older stateless tokens cannot be individually tracked; invalidate their version.
        revoke_all_sessions(db, user)
    db.commit()
