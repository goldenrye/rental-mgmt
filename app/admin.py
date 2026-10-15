from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import selectinload

from app.account_access import ensure_access, revoke_all_sessions, utc
from app.deps import DbSession, SuperAdmin
from app.models import AdminAuditEvent, LoginSession, User, UserAccess, UserRole
from app.schemas import AdminPasswordReset, LandlordAdminRead, LandlordStatusUpdate
from app.security import hash_password


router = APIRouter(prefix="/admin", tags=["Landlord administration"])


def landlord_or_404(db: DbSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != UserRole.landlord.value:
        raise HTTPException(status_code=404, detail="Landlord not found")
    return user


@router.get("/landlords", response_model=list[LandlordAdminRead])
def list_landlords(db: DbSession, admin: SuperAdmin) -> list[LandlordAdminRead]:
    users = db.scalars(select(User).where(User.role == UserRole.landlord.value).options(
        selectinload(User.access_state), selectinload(User.landlord_profile),
    ).order_by(User.id)).all()
    now = datetime.now(timezone.utc)
    sessions = {
        row.user_id: row for row in db.execute(select(
            LoginSession.user_id,
            func.max(LoginSession.last_seen_at).label("last_seen"),
            func.sum(case((and_(
                LoginSession.revoked_at.is_(None),
                LoginSession.expires_at > now,
                LoginSession.token_version == UserAccess.token_version,
                UserAccess.is_active.is_(True),
            ), 1), else_=0)).label("active"),
        ).join(UserAccess, UserAccess.user_id == LoginSession.user_id)
          .group_by(LoginSession.user_id))
    }
    result = []
    for user in users:
        state = user.access_state
        session = sessions.get(user.id)
        active = int(session.active) if session else 0
        last_login = utc(state.last_login_at) if state and state.last_login_at else None
        result.append(LandlordAdminRead(
            user_id=user.id,
            landlord_id=user.landlord_profile.id if user.landlord_profile else None,
            full_name=user.full_name, email=user.email,
            is_active=state.is_active if state else True,
            last_login_at=last_login,
            last_seen_at=utc(session.last_seen) if session and session.last_seen else None,
            active_sessions=active,
            login_status="signed_in" if active else ("signed_out" if last_login else "never_logged_in"),
            created_at=utc(user.created_at),
        ))
    return result


@router.patch("/landlords/{user_id}/status", status_code=204)
def set_landlord_status(user_id: int, payload: LandlordStatusUpdate, db: DbSession, admin: SuperAdmin) -> Response:
    user = landlord_or_404(db, user_id)
    state = ensure_access(db, user)
    if state.is_active != payload.is_active:
        state.is_active = payload.is_active
        if not payload.is_active:
            revoke_all_sessions(db, user)
        db.add(AdminAuditEvent(
            actor_id=admin.id, target_id=user.id,
            action="activate" if payload.is_active else "deactivate",
        ))
        db.commit()
    return Response(status_code=204)


@router.post("/landlords/{user_id}/password", status_code=204)
def reset_landlord_password(user_id: int, payload: AdminPasswordReset, db: DbSession, admin: SuperAdmin) -> Response:
    user = landlord_or_404(db, user_id)
    user.hashed_password = hash_password(payload.new_password)
    revoke_all_sessions(db, user)
    db.add(AdminAuditEvent(actor_id=admin.id, target_id=user.id, action="reset_password"))
    db.commit()
    return Response(status_code=204)
