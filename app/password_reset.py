"""Self-service password reset by emailed single-use link. Only a SHA-256 hash of each code is stored.

The request endpoint answers the same way whether or not the email is registered, and sends mail
after the response, so neither the reply nor its timing reveals which accounts exist."""

import hashlib
import logging
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from sqlalchemy import func, select, update

from app.account_access import ensure_access, revoke_all_sessions
from app.config import get_settings
from app.deps import DbSession
from app.login_throttle import client_ip
from app.models import PasswordResetToken, User, UserRole
from app.schemas import PasswordResetConfirm, PasswordResetRequest
from app.security import hash_password

logger = logging.getLogger(__name__)

RESET_TTL = timedelta(hours=1)
# Bounds how many reset emails one account can be sent, whoever asks for them.
MAX_REQUESTS_PER_HOUR = 3
REQUEST_ACCEPTED = "If an account exists for that email, a password reset link has been sent. It expires in 1 hour."
INVALID_RESET = "This password reset link is invalid, expired, or already used. Request a new one."

router = APIRouter(prefix="/auth/password-reset", tags=["Password reset"])


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def email_configured() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_from and app_url())


def app_url() -> str:
    settings = get_settings()
    return (settings.app_url or settings.google_calendar_app_url).rstrip("/")


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with smtp:
        if settings.smtp_port != 465 and settings.smtp_starttls:
            smtp.starttls(context=context)
        password = settings.smtp_password.get_secret_value()
        if settings.smtp_username and password:
            smtp.login(settings.smtp_username, password)
        smtp.send_message(message)


def send_reset_email(to: str, name: str, code: str) -> None:
    link = f"{app_url()}/#reset={code}"
    body = (
        f"Hi {name},\n\n"
        "Someone asked to reset the password for your Rental Management account.\n"
        f"To choose a new password, open this link within 1 hour:\n\n{link}\n\n"
        "The link works once. If you didn't ask for this, ignore this email; your password stays the same.\n"
    )
    try:
        send_email(to, "Reset your Rental Management password", body)
    except Exception:
        # The requester already got the generic reply; never log the link, which is a credential.
        logger.exception("Password reset email failed for user")


def resettable_user(db: DbSession, email: str) -> User | None:
    user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    # The super admin signs in with a username, not a mailbox, and deactivated or unapproved accounts stay locked.
    if user is None or user.role == UserRole.super_admin.value:
        return None
    state = ensure_access(db, user)
    if not state.is_active:
        return None
    return user


@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: PasswordResetRequest, db: DbSession, request: Request, tasks: BackgroundTasks) -> dict[str, str]:
    if not email_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset by email is not set up on this server. Ask the administrator to reset your password.",
        )
    user = resettable_user(db, str(payload.email))
    if user is None:
        return {"detail": REQUEST_ACCEPTED}
    now = datetime.now(timezone.utc)
    recent = db.scalar(select(func.count()).select_from(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id, PasswordResetToken.created_at > now - timedelta(hours=1),
    ))
    if recent >= MAX_REQUESTS_PER_HOUR:
        return {"detail": REQUEST_ACCEPTED}
    code = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id, code_hash=hash_code(code), created_at=now, expires_at=now + RESET_TTL,
        requested_ip=client_ip(request)[:64],
    ))
    db.commit()
    tasks.add_task(send_reset_email, user.email, user.display_name or user.full_name, code)
    return {"detail": REQUEST_ACCEPTED}


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(payload: PasswordResetConfirm, db: DbSession) -> Response:
    now = datetime.now(timezone.utc)
    token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.code_hash == hash_code(payload.token)))
    if token is None:
        raise HTTPException(status_code=400, detail=INVALID_RESET)
    # Claim in SQL so two concurrent submissions of one link cannot both succeed.
    claimed = db.execute(update(PasswordResetToken).where(
        PasswordResetToken.id == token.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).values(used_at=now).execution_options(synchronize_session=False))
    user = db.get(User, token.user_id)
    if claimed.rowcount != 1 or user is None or not ensure_access(db, user).is_active:
        db.rollback()
        raise HTTPException(status_code=400, detail=INVALID_RESET)
    user.hashed_password = hash_password(payload.new_password)
    ensure_access(db, user).password_change_required = False
    # A reset means the old password may be known to someone else: end every session and API key.
    revoke_all_sessions(db, user)
    db.execute(update(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None),
    ).values(used_at=now).execution_options(synchronize_session=False))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
