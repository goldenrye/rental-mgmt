"""WebAuthn ceremonies. Only public keys are stored; all successful logins use tracked sessions."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from webauthn import (
    generate_authentication_options, generate_registration_options, options_to_json,
    verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
    ResidentKeyRequirement, UserVerificationRequirement,
)

from app.account_access import ensure_access, issue_login, validate_account_access
from app.config import get_settings
from app.deps import BearerToken, CurrentUser, DbSession
from app.login_throttle import client_ip, ensure_not_locked, login_key, record_failure, clear_failures
from app.models import PasskeyChallenge, PasskeyCredential, PasskeyUser, User, UserAccess
from app.security import decode_access_token, verify_password

router = APIRouter(tags=["Passkeys"])
INVALID = "Passkey verification failed. Try again or sign in with your password."
UNAVAILABLE = "Passkeys are not configured on this server."
TTL = timedelta(minutes=5)


class ConfirmPassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)


class RegistrationStart(ConfirmPassword):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a passkey name")
        return value.strip()


class CeremonyFinish(BaseModel):
    ceremony_id: str = Field(min_length=32, max_length=64)
    credential: dict

    @field_validator("credential")
    @classmethod
    def bounded_credential(cls, value):
        if len(json.dumps(value)) > 32768:
            raise ValueError("Credential is too large")
        return value


def configuration() -> tuple[str, list[str]]:
    settings = get_settings()
    rp = settings.passkey_rp_id.strip().lower()
    origins = [value.strip() for value in settings.passkey_origins.split(",") if value.strip()]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", rp) or not origins:
        raise HTTPException(503, UNAVAILABLE)
    for origin in origins:
        url = urlsplit(origin)
        # Exact origins, no wildcard or request-header inference. localhost permits dev WebAuthn.
        if (url.scheme != "https" and not (url.scheme == "http" and rp == "localhost")) or (
            url.hostname != rp and not (url.hostname or "").endswith("." + rp)
        ) or url.path or url.query or url.fragment or url.username or url.password:
            raise HTTPException(503, UNAVAILABLE)
    return rp, origins


@router.get("/auth/passkeys/status")
def passkey_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        rp, _ = configuration()
        return {"available": True, "rp_id": rp}
    except HTTPException:
        return {"available": False, "rp_id": None}


@router.get("/.well-known/apple-app-site-association")
def apple_association():
    configuration()
    apps = [value.strip() for value in get_settings().passkey_apple_app_ids.split(",") if value.strip()]
    if not apps or any(not re.fullmatch(r"[A-Z0-9]{10}\.[A-Za-z0-9.-]+", value) for value in apps):
        raise HTTPException(404, "Apple app association is not configured")
    return {"webcredentials": {"apps": apps}}


def active_account(db, user):
    if user is None:
        raise HTTPException(400, INVALID)
    state = db.scalar(select(UserAccess).where(UserAccess.user_id == user.id).with_for_update()
                      .execution_options(populate_existing=True))
    if state is None:
        state = ensure_access(db, user)
    if not state.is_active or state.password_change_required:
        raise HTTPException(403, "Sign in with your password and complete any required password change first.")
    return state


def confirm_password(db, user, password, request):
    key = login_key("passkey:" + str(user.id), client_ip(request))
    ensure_not_locked(db, key)
    if not verify_password(password, user.hashed_password):
        record_failure(db, key)
        raise HTTPException(400, "Current password is incorrect")
    clear_failures(db, key)


def new_challenge(db, request, purpose, rp, **values):
    now = datetime.now(timezone.utc)
    requester = hashlib.sha256(client_ip(request).encode()).hexdigest()
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < now - timedelta(hours=1)))
    count = db.scalar(select(func.count()).select_from(PasskeyChallenge).where(
        PasskeyChallenge.requester == requester, PasskeyChallenge.created_at > now - TTL,
    ))
    if count >= 30:
        raise HTTPException(429, "Too many passkey requests. Try again later.")
    item = PasskeyChallenge(
        id=secrets.token_urlsafe(32), challenge=secrets.token_bytes(32), purpose=purpose,
        rp_id=rp, requester=requester, created_at=now, expires_at=now + TTL, **values,
    )
    db.add(item)
    db.flush()
    return item


def claim(db, ceremony_id, purpose, rp, user_id=None, session_id=None):
    now = datetime.now(timezone.utc)
    # Commit the claim even if subsequent verification fails, so failures cannot be replayed.
    item = db.scalar(select(PasskeyChallenge).where(PasskeyChallenge.id == ceremony_id))
    result = db.execute(update(PasskeyChallenge).where(
        PasskeyChallenge.id == ceremony_id, PasskeyChallenge.purpose == purpose,
        PasskeyChallenge.rp_id == rp, PasskeyChallenge.user_id == user_id,
        PasskeyChallenge.session_id == session_id,
        PasskeyChallenge.used_at.is_(None), PasskeyChallenge.expires_at > now,
    ).values(used_at=now).execution_options(synchronize_session=False))
    db.commit()
    if result.rowcount != 1 or item is None:
        raise HTTPException(400, INVALID)
    return item


def check_client_data(credential):
    data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
    if data.get("crossOrigin", False) is not False or data.get("topOrigin"):
        raise ValueError("Cross-origin ceremony not allowed")


@router.get("/me/passkeys")
def list_passkeys(db: DbSession, user: CurrentUser):
    return [{"id": key.id, "name": key.name, "created_at": key.created_at, "last_used_at": key.last_used_at}
            for key in db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)
                                  .order_by(PasskeyCredential.created_at))]


@router.post("/me/passkeys/register/options")
def registration_options(payload: RegistrationStart, db: DbSession, user: CurrentUser,
                         token: BearerToken, request: Request, response: Response):
    rp, _ = configuration()
    confirm_password(db, user, payload.current_password, request)
    state = active_account(db, user)
    keys = list(db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)))
    if len(keys) >= 10:
        raise HTTPException(400, "Remove an existing passkey before adding another.")
    identity = db.get(PasskeyUser, user.id)
    if identity is None:
        # Serializes registration for this account through its UserAccess lock.
        identity = PasskeyUser(user_id=user.id, handle=secrets.token_bytes(32))
        db.add(identity)
        db.flush()
    claims = decode_access_token(token)
    item = new_challenge(db, request, "register", rp, user_id=user.id, token_version=state.token_version,
                         session_id=claims.get("jti"), name=payload.name)
    options = generate_registration_options(
        rp_id=rp, rp_name="Rental Management", user_name=user.email, user_id=identity.handle,
        user_display_name=user.display_name or user.full_name, challenge=item.challenge,
        supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=key.credential_id) for key in keys if key.rp_id == rp],
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"ceremony_id": item.id, "publicKey": json.loads(options_to_json(options))}


@router.post("/me/passkeys/register/verify", status_code=201)
def registration_verify(payload: CeremonyFinish, db: DbSession, user: CurrentUser, token: BearerToken):
    rp, origins = configuration()
    claims = decode_access_token(token)
    item = claim(db, payload.ceremony_id, "register", rp, user.id, claims.get("jti"))
    try:
        check_client_data(payload.credential)
        verified = verify_registration_response(
            credential=payload.credential, expected_challenge=item.challenge, expected_rp_id=rp,
            expected_origin=origins, require_user_verification=True,
            supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256],
        )
    except Exception as exc:
        raise HTTPException(400, INVALID) from exc
    try:
        validate_account_access(db, user, claims)
    except ValueError as exc:
        raise HTTPException(401, INVALID) from exc
    state = active_account(db, user)
    if state.token_version != item.token_version:
        raise HTTPException(400, INVALID)
    if db.scalar(select(func.count()).select_from(PasskeyCredential).where(PasskeyCredential.user_id == user.id)) >= 10:
        raise HTTPException(400, "Remove an existing passkey before adding another.")
    key = PasskeyCredential(
        id=secrets.token_hex(16), user_id=user.id, credential_id=verified.credential_id,
        public_key=verified.credential_public_key, rp_id=rp, name=item.name,
        sign_count=verified.sign_count, created_at=datetime.now(timezone.utc),
    )
    db.add(key)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This passkey is already registered.") from exc
    return {"id": key.id}


@router.post("/auth/passkeys/options")
def authentication_options(db: DbSession, request: Request, response: Response):
    rp, _ = configuration()
    item = new_challenge(db, request, "authenticate", rp)
    options = generate_authentication_options(
        rp_id=rp, challenge=item.challenge, user_verification=UserVerificationRequirement.REQUIRED,
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"ceremony_id": item.id, "publicKey": json.loads(options_to_json(options))}


@router.post("/auth/passkeys/verify")
def authentication_verify(payload: CeremonyFinish, db: DbSession, response: Response):
    rp, origins = configuration()
    item = claim(db, payload.ceremony_id, "authenticate", rp)
    try:
        check_client_data(payload.credential)
        key = db.scalar(select(PasskeyCredential).where(
            PasskeyCredential.credential_id == base64url_to_bytes(payload.credential["id"]),
            PasskeyCredential.rp_id == rp,
        ))
        if key is None:
            raise ValueError("Unknown credential")
        identity = db.get(PasskeyUser, key.user_id)
        if identity is None or base64url_to_bytes(payload.credential["response"]["userHandle"]) != identity.handle:
            raise ValueError("User handle mismatch")
        verified = verify_authentication_response(
            credential=payload.credential, expected_challenge=item.challenge, expected_rp_id=rp,
            expected_origin=origins, credential_public_key=key.public_key,
            credential_current_sign_count=key.sign_count, require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(400, INVALID) from exc
    user = db.get(User, key.user_id)
    active_account(db, user)
    # The conditional write detects concurrent counter advances, deletion, or reset.
    result = db.execute(update(PasskeyCredential).where(
        PasskeyCredential.id == key.id, PasskeyCredential.sign_count == key.sign_count,
    ).values(sign_count=verified.new_sign_count, last_used_at=datetime.now(timezone.utc))
      .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(400, INVALID)
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": issue_login(db, user, "passkey"), "token_type": "bearer"}


@router.delete("/me/passkeys/{key_id}", status_code=204)
def remove_passkey(key_id: str, payload: ConfirmPassword, db: DbSession, user: CurrentUser, request: Request):
    confirm_password(db, user, payload.current_password, request)
    active_account(db, user)
    result = db.execute(delete(PasskeyCredential).where(
        PasskeyCredential.id == key_id, PasskeyCredential.user_id == user.id,
    ))
    if result.rowcount != 1:
        raise HTTPException(404, "Passkey not found")
    db.commit()
    return Response(status_code=204)
