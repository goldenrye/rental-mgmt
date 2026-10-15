from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LandlordProfile, User, UserRole
from app.security import decode_access_token
from app.account_access import validate_account_access


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
DbSession = Annotated[Session, Depends(get_db)]
BearerToken = Annotated[str, Depends(oauth2_scheme)]


def get_current_user(db: DbSession, token: BearerToken) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdecimal():
            raise credentials_error
        user_id = int(user_id)
        if not 0 < user_id <= 9223372036854775807:
            raise credentials_error
    except (jwt.PyJWTError, ValueError) as exc:
        raise credentials_error from exc

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error
    try:
        validate_account_access(db, user, payload)
    except ValueError as exc:
        raise credentials_error from exc
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_super_admin(user: CurrentUser) -> User:
    if user.role != UserRole.super_admin.value:
        raise HTTPException(status_code=403, detail="Super admin access required")
    if user.password_change_required:
        raise HTTPException(status_code=403, detail="Change the initial admin password first")
    return user


SuperAdmin = Annotated[User, Depends(require_super_admin)]


def require_landlord(user: CurrentUser) -> User:
    if user.role != UserRole.landlord.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Landlord access required")
    return user


LandlordUser = Annotated[User, Depends(require_landlord)]


def get_landlord_profile(db: DbSession, user: LandlordUser) -> LandlordProfile:
    profile = user.landlord_profile
    if profile is None:
        profile = LandlordProfile(user_id=user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


Landlord = Annotated[LandlordProfile, Depends(get_landlord_profile)]
