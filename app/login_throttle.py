"""Password authentication with per-account lockout after repeated failures."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.account_access import utc
from app.models import LoginThrottle, User
from app.security import hash_password, verify_password


MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)
# Verified when the user does not exist, so response time does not reveal registered emails.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def login_key(identifier: str) -> str:
    return identifier.strip().lower()[:255]


def ensure_not_locked(db: Session, key: str) -> None:
    throttle = db.get(LoginThrottle, key)
    if throttle is None or throttle.locked_until is None:
        return
    remaining = utc(throttle.locked_until) - datetime.now(timezone.utc)
    if remaining.total_seconds() > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(int(remaining.total_seconds()) + 1)},
        )


def record_failure(db: Session, key: str) -> None:
    now = datetime.now(timezone.utc)
    throttle = db.get(LoginThrottle, key)
    if throttle is None:
        try:
            with db.begin_nested():
                db.add(LoginThrottle(login_key=key, failures=1, window_started_at=now))
            db.commit()
            return
        except IntegrityError:
            throttle = db.get(LoginThrottle, key)
            if throttle is None:
                raise
    if utc(throttle.window_started_at) <= now - FAILURE_WINDOW:
        throttle.failures, throttle.window_started_at, throttle.locked_until = 1, now, None
        db.commit()
        return
    # Increment in SQL so concurrent failures are all counted.
    db.execute(update(LoginThrottle).where(LoginThrottle.login_key == key).values(
        failures=LoginThrottle.failures + 1,
    ))
    db.refresh(throttle)
    if throttle.failures >= MAX_FAILURES:
        throttle.locked_until = now + LOCKOUT
    db.commit()


def clear_failures(db: Session, key: str) -> None:
    throttle = db.get(LoginThrottle, key)
    if throttle is not None:
        db.delete(throttle)
        db.commit()


def authenticate(db: Session, identifier: str, password: str) -> Optional[User]:
    """Return the user for valid credentials, None for invalid ones; raise 429 while locked out."""
    key = login_key(identifier)
    ensure_not_locked(db, key)
    user = db.scalar(select(User).where(User.email == identifier))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        record_failure(db, key)
        return None
    if not verify_password(password, user.hashed_password):
        record_failure(db, key)
        return None
    clear_failures(db, key)
    return user
