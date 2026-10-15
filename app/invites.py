"""Single-use landlord invitations: an invited sign-up is active immediately, skipping admin approval."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.account_access import utc
from app.models import LandlordInvite, User
from app.schemas import InviteRead

INVALID_INVITE = "This invitation link is invalid, expired, or already used. Ask the administrator for a new one."


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_invite(db: Session, admin: User, email: str | None, expires_in_days: int) -> tuple[LandlordInvite, str]:
    code = secrets.token_urlsafe(24)
    invite = LandlordInvite(
        code_hash=hash_code(code), email=email.lower() if email else None, created_by_id=admin.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite, code


def invite_status(invite: LandlordInvite) -> str:
    if invite.revoked_at:
        return "revoked"
    if invite.used_at:
        return "used"
    if utc(invite.expires_at) <= datetime.now(timezone.utc):
        return "expired"
    return "active"


def invite_read(invite: LandlordInvite) -> InviteRead:
    return InviteRead(
        id=invite.id, email=invite.email, created_at=utc(invite.created_at), expires_at=utc(invite.expires_at),
        used_at=utc(invite.used_at) if invite.used_at else None,
        used_by_email=invite.used_by.email if invite.used_by else None,
        revoked_at=utc(invite.revoked_at) if invite.revoked_at else None,
        status=invite_status(invite),
    )


def find_usable_invite(db: Session, code: str, email: str) -> LandlordInvite:
    """Return the invitation for code if it can register email; raise ValueError otherwise."""
    invite = db.scalar(select(LandlordInvite).where(LandlordInvite.code_hash == hash_code(code)))
    if invite is None or invite_status(invite) != "active":
        raise ValueError(INVALID_INVITE)
    if invite.email and invite.email != email.lower():
        raise ValueError("This invitation was issued for a different email address.")
    return invite


def claim_invite(db: Session, invite: LandlordInvite, user: User) -> None:
    """Mark the invitation used by user in the caller's transaction; raise ValueError if another sign-up won."""
    now = datetime.now(timezone.utc)
    result = db.execute(update(LandlordInvite).where(
        LandlordInvite.id == invite.id,
        LandlordInvite.used_at.is_(None),
        LandlordInvite.revoked_at.is_(None),
        LandlordInvite.expires_at > now,
    ).values(used_at=now, used_by_id=user.id).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ValueError(INVALID_INVITE)
