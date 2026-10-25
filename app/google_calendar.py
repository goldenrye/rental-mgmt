"""Opt-in, one-way rent exports. Google credentials never reach the frontend."""

import asyncio
import hashlib
import hmac
import html
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import get_settings
from app.database import SessionLocal
from app.deps import DbSession, Landlord
from app.encryption import EncryptionError, encode, master_key, seal, unseal
from app.models import (
    GoogleCalendarConnection, GoogleCalendarExport, LandlordProfile, RentalBill, UserAccess,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Google Calendar"])
SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
COOKIE = "rental_google_oauth"
POLL_SECONDS = 60


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def valid_url(value):
    try:
        url = urlsplit(value)
        return bool(
            url.hostname and not url.username and not url.password and not url.query and not url.fragment
            and (url.scheme == "https" or (
                url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
            ))
        )
    except ValueError:
        return False


def configured():
    settings = get_settings()
    return bool(
        settings.google_calendar_client_id and settings.google_calendar_client_secret.get_secret_value()
        and valid_url(settings.google_calendar_redirect_uri)
        and valid_url(settings.google_calendar_app_url)
    )


def require_configured():
    if not configured():
        raise HTTPException(503, "Google Calendar is not configured on this server.")


def encrypt_secret(landlord_id, kind, content):
    return seal(master_key(), json.dumps(content).encode(), f"google-calendar:{landlord_id}:{kind}")


def decrypt_secret(landlord_id, kind, content):
    return json.loads(unseal(master_key(), content, f"google-calendar:{landlord_id}:{kind}"))


def lock_connection(db, landlord_id):
    # Both enqueue and delivery use this lock. Once disabling returns, no new
    # exports can start. SQLite needs a write before its read.
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    db.execute(insert(GoogleCalendarConnection).values(landlord_id=landlord_id, enabled=False)
               .on_conflict_do_nothing(index_elements=["landlord_id"]))
    if db.get_bind().dialect.name == "sqlite":
        db.execute(update(GoogleCalendarConnection).where(
            GoogleCalendarConnection.landlord_id == landlord_id,
        ).values(enabled=GoogleCalendarConnection.enabled))
    return db.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.landlord_id == landlord_id,
    ).with_for_update().execution_options(populate_existing=True))


def cancel_pending(db, landlord_id):
    db.execute(update(GoogleCalendarExport).where(
        GoogleCalendarExport.landlord_id == landlord_id,
        GoogleCalendarExport.status == "pending",
    ).values(status="skipped"))


def clear_oauth(connection):
    connection.oauth_state_hash = None
    connection.oauth_browser_hash = None
    connection.oauth_verifier = None
    connection.oauth_expires_at = None
    connection.oauth_token_version = None


class GoogleFailure(Exception):
    def __init__(self, *, reconnect=False):
        self.reconnect = reconnect
        super().__init__("Google Calendar request failed")


class GoogleClient:
    def __enter__(self):
        self.http = httpx.Client(timeout=10, follow_redirects=False)
        return self

    def __exit__(self, *args):
        self.http.close()

    def request(self, method, url, *, accepted=(), **kwargs):
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.RequestError:
            raise GoogleFailure() from None
        if response.status_code in accepted:
            return response.status_code, {}
        if not response.is_success:
            try:
                error = response.json().get("error")
            except (ValueError, AttributeError):
                error = None
            raise GoogleFailure(reconnect=response.status_code == 401 or error == "invalid_grant")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError()
            return response.status_code, payload
        except ValueError:
            raise GoogleFailure() from None

    def token(self, **fields):
        settings = get_settings()
        _, result = self.request("POST", "https://oauth2.googleapis.com/token", data={
            "client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret.get_secret_value(),
            **fields,
        })
        if not result.get("access_token") or SCOPE not in result.get("scope", SCOPE).split():
            raise GoogleFailure(reconnect=True)
        return result

    def calendar(self, access_token, existing_id=None):
        headers = {"Authorization": f"Bearer {access_token}"}
        if existing_id:
            code, _ = self.request("GET", f"{CALENDAR_API}/calendars/{quote(existing_id, safe='')}",
                                   headers=headers, accepted=(403, 404))
            if code == 200:
                return existing_id
        _, result = self.request("POST", f"{CALENDAR_API}/calendars", headers=headers, json={
            "summary": "Rental Due Dates",
            "description": "Rent due dates exported by Rental Management. Managed independently of bill payments.",
        })
        if not result.get("id"):
            raise GoogleFailure()
        return result["id"]

    def insert_event(self, access_token, calendar_id, event):
        # The persisted random ID survives retries, including a timeout after Google
        # accepted the insert. 409 means that exact export already exists.
        self.request("POST", f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events",
                     headers={"Authorization": f"Bearer {access_token}"},
                     params={"sendUpdates": "none"}, json=event, accepted=(409,))


def credentials_from(tokens, refresh_token=None):
    refresh = tokens.get("refresh_token") or refresh_token
    if not refresh:
        raise GoogleFailure(reconnect=True)
    try:
        expires = now() + timedelta(seconds=max(0, int(tokens.get("expires_in", 3600))) - 60)
    except (ValueError, TypeError, OverflowError):
        raise GoogleFailure() from None
    return {"refresh_token": refresh, "access_token": tokens["access_token"], "expires_at": expires.timestamp()}


def status_payload(db, landlord_id):
    connection = db.get(GoogleCalendarConnection, landlord_id)
    pending = db.scalar(select(func.count()).select_from(GoogleCalendarExport).where(
        GoogleCalendarExport.landlord_id == landlord_id, GoogleCalendarExport.status == "pending",
    ))
    return {
        "available": configured(),
        "enabled": bool(connection and connection.enabled),
        "connected": bool(connection and connection.credentials and connection.calendar_id),
        "calendar_name": "Rental Due Dates" if connection and connection.calendar_id else None,
        "pending_exports": pending,
        "last_synced_at": connection.last_synced_at if connection else None,
        "last_error": connection.last_error if connection else None,
    }


class Preference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.get("/me/google-calendar")
def calendar_status(db: DbSession, landlord: Landlord, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return status_payload(db, landlord.id)


@router.put("/me/google-calendar")
def calendar_preference(payload: Preference, db: DbSession, landlord: Landlord, response: Response):
    response.headers["Cache-Control"] = "no-store"
    connection = lock_connection(db, landlord.id)
    if payload.enabled:
        require_configured()
        if not connection.credentials or not connection.calendar_id:
            raise HTTPException(409, "Connect Google Calendar before enabling automatic exports.")
    else:
        cancel_pending(db, landlord.id)
    connection.enabled = payload.enabled
    db.commit()
    return status_payload(db, landlord.id)


@router.post("/me/google-calendar/connect")
def calendar_connect(db: DbSession, landlord: Landlord, response: Response):
    require_configured()
    connection = lock_connection(db, landlord.id)
    if connection.enabled:
        raise HTTPException(409, "Turn off automatic exports before reconnecting Google Calendar.")
    state, browser, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    try:
        encrypted_verifier = encrypt_secret(landlord.id, "verifier", verifier)
    except EncryptionError:
        raise HTTPException(503, "Configure the server encryption key before connecting Google Calendar.") from None
    access = db.get(UserAccess, landlord.user_id)
    connection.oauth_state_hash = digest(state)
    connection.oauth_browser_hash = digest(browser)
    connection.oauth_verifier = encrypted_verifier
    connection.oauth_expires_at = now() + timedelta(minutes=10)
    connection.oauth_token_version = access.token_version if access else 0
    db.commit()
    settings = get_settings()
    response.set_cookie(COOKIE, browser, max_age=600, httponly=True, samesite="lax",
                        secure=settings.google_calendar_redirect_uri.startswith("https://"), path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_calendar_client_id,
        "redirect_uri": settings.google_calendar_redirect_uri,
        "response_type": "code", "scope": SCOPE, "access_type": "offline",
        "prompt": "consent select_account", "state": state,
        "code_challenge": encode(hashlib.sha256(verifier.encode()).digest()).rstrip("="),
        "code_challenge_method": "S256",
    })}


def callback_page(message, success=False):
    app_url = get_settings().google_calendar_app_url
    link = f'<p><a href="{html.escape(app_url, quote=True)}">Return to Rental Management</a></p>' if valid_url(app_url) else ""
    response = HTMLResponse(
        "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Google Calendar connection</title><body><h1>Google Calendar</h1>"
        f"<p>{html.escape(message)}</p>{link}</body></html>",
        status_code=200 if success else 400,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"},
    )
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/google-calendar/callback", response_class=HTMLResponse)
def calendar_callback(
    request: Request, db: DbSession,
    state: str = Query(default="", max_length=128),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=256),
):
    require_configured()
    candidate = db.scalar(select(GoogleCalendarConnection.landlord_id).where(
        GoogleCalendarConnection.oauth_state_hash == digest(state),
    ))
    if candidate is None:
        return callback_page("This connection request is invalid or expired. Start again from your profile.")
    connection = lock_connection(db, candidate)
    landlord = db.get(LandlordProfile, candidate)
    access = db.get(UserAccess, landlord.user_id) if landlord else None
    if (
        connection.oauth_state_hash != digest(state) or not connection.oauth_expires_at
        or aware(connection.oauth_expires_at) <= now()
        or not connection.oauth_browser_hash
        or not hmac.compare_digest(connection.oauth_browser_hash, digest(request.cookies.get(COOKIE, "")))
        or not landlord or not landlord.user.is_active or landlord.user.role != "landlord"
        or connection.oauth_token_version != (access.token_version if access else 0)
    ):
        return callback_page("This connection request is invalid or expired. Start again from your profile.")
    try:
        verifier = decrypt_secret(candidate, "verifier", connection.oauth_verifier)
    except EncryptionError:
        clear_oauth(connection)
        db.commit()
        return callback_page("The server encryption key is unavailable or changed. Ask the administrator to check it.")
    clear_oauth(connection)
    if error or not code:
        db.commit()
        return callback_page("Google Calendar connection was cancelled. Automatic exports remain off.")
    try:
        with GoogleClient() as google:
            tokens = google.token(grant_type="authorization_code", code=code, code_verifier=verifier,
                                  redirect_uri=get_settings().google_calendar_redirect_uri)
            credentials = credentials_from(tokens)
            calendar_id = google.calendar(credentials["access_token"], connection.calendar_id)
        connection.credentials = encrypt_secret(candidate, "credentials", credentials)
        connection.calendar_id = calendar_id
        connection.enabled = False
        connection.last_error = None
        cancel_pending(db, candidate)
        db.commit()
    except (GoogleFailure, EncryptionError):
        connection.last_error = "Google connection failed. Please connect again and grant calendar permission."
        db.commit()
        return callback_page(connection.last_error)
    return callback_page(
        "Connected. Automatic exports are still off. Close this tab, refresh calendar status in your profile, "
        "and enable automatic exports when you are ready.", success=True,
    )


@router.delete("/me/google-calendar", status_code=204)
def calendar_disconnect(db: DbSession, landlord: Landlord):
    connection = lock_connection(db, landlord.id)
    connection.enabled = False
    connection.credentials = None
    connection.last_error = None
    clear_oauth(connection)
    cancel_pending(db, landlord.id)
    db.commit()
    # Keep the calendar ID so reconnecting the same account reuses its calendar.
    # Do not revoke a grant shared by another landlord using the same Google account.
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def enqueue_rent_exports(db, bills):
    """Called only for newly inserted bills, before their transaction commits."""
    connections = {}
    for bill in bills:
        if bill.bill_type != "rent" or bill.source_property_bill_id is not None:
            continue
        if bill.landlord_id not in connections:
            # Default-off landlords need no integration row or secret.
            existing = db.get(GoogleCalendarConnection, bill.landlord_id)
            connections[bill.landlord_id] = lock_connection(db, bill.landlord_id) if existing else None
        connection = connections[bill.landlord_id]
        if not connection or not connection.enabled or not connection.credentials or not connection.calendar_id:
            continue
        db.add(GoogleCalendarExport(id=uuid.uuid4().hex, bill=bill, landlord_id=bill.landlord_id,
                                   status="pending", next_attempt_at=now()))


def event_for(job, bill):
    property_ = bill.lease.property
    tenant = bill.tenant.full_name
    unit = bill.lease.unit.name if bill.lease.unit else "Whole property"
    return {
        "id": job.id,
        "summary": f"Rent due: {tenant} - {property_.name}",
        "description": (
            f"Rental bill #{bill.id}\nTenant: {tenant}\nProperty: {property_.name}\nUnit: {unit}\n"
            f"Amount due: ${bill.amount_due:.2f}\nBilling month: {bill.bill_year}-{bill.bill_month:02d}\n"
            "Exported when generated. Later payments or deletions are not synchronized.\n"
            f"Rental Management: {get_settings().google_calendar_app_url}"
        ),
        "start": {"date": bill.due_date.isoformat()},
        "end": {"date": (bill.due_date + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "visibility": "private",
        "reminders": {"useDefault": False},
    }


def deliver_export(db, job_id):
    job = db.get(GoogleCalendarExport, job_id)
    if not job:
        return
    connection = lock_connection(db, job.landlord_id)
    db.refresh(job)
    if job.status != "pending" or aware(job.next_attempt_at) > now():
        return
    landlord = db.get(LandlordProfile, job.landlord_id)
    if (
        not connection.enabled or not connection.credentials or not connection.calendar_id
        or not landlord or not landlord.user.is_active or landlord.user.role != "landlord"
    ):
        job.status = "skipped"
        return
    bill = db.get(RentalBill, job.bill_id)
    if not bill or bill.landlord_id != job.landlord_id or bill.bill_type != "rent" or bill.source_property_bill_id:
        job.status = "skipped"
        return
    try:
        credentials = decrypt_secret(job.landlord_id, "credentials", connection.credentials)
        with GoogleClient() as google:
            if credentials["expires_at"] <= now().timestamp():
                tokens = google.token(grant_type="refresh_token", refresh_token=credentials["refresh_token"])
                credentials = credentials_from(tokens, credentials["refresh_token"])
                connection.credentials = encrypt_secret(job.landlord_id, "credentials", credentials)
            google.insert_event(credentials["access_token"], connection.calendar_id, event_for(job, bill))
        job.status = "sent"
        connection.last_synced_at = now()
        connection.last_error = None
    except GoogleFailure as exc:
        job.attempts += 1
        job.next_attempt_at = now() + timedelta(seconds=min(3600, 60 * 2 ** min(job.attempts, 6)))
        if exc.reconnect:
            connection.enabled = False
            connection.credentials = None
            connection.last_error = "Google authorization expired or was revoked. Reconnect and enable exports again."
            cancel_pending(db, job.landlord_id)
        else:
            connection.last_error = "Google Calendar is temporarily unavailable or inaccessible. Exports will retry automatically."
    except EncryptionError:
        # Do not discard credentials when the deployment key needs restoring.
        job.attempts += 1
        job.next_attempt_at = now() + timedelta(hours=1)
        connection.last_error = "Calendar credentials could not be decrypted. Ask the administrator to check the server key."


def reconcile_calendar_exports(session_factory=SessionLocal):
    if not configured():
        return
    with session_factory() as db:
        ids = list(db.scalars(select(GoogleCalendarExport.id).where(
            GoogleCalendarExport.status == "pending", GoogleCalendarExport.next_attempt_at <= now(),
        ).order_by(GoogleCalendarExport.next_attempt_at, GoogleCalendarExport.id).limit(100)))
    for job_id in ids:
        try:
            with session_factory() as db:
                deliver_export(db, job_id)
                db.commit()
        except Exception as exc:
            # No Google payloads, tenant data, tokens, or exception messages in logs.
            logger.error("Calendar export failed: job=%s error=%s", job_id, type(exc).__name__)


async def calendar_export_scheduler():
    while True:
        try:
            await asyncio.to_thread(reconcile_calendar_exports)
        except Exception as exc:
            logger.error("Calendar scan failed: error=%s", type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)
