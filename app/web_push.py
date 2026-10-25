"""Opt-in browser push reminders for lease ends and bill due dates.

Payloads carry only counts and dates: notifications can appear on a locked screen,
so tenant names, property names and amounts stay in the app.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import APIRouter, HTTPException, Response
from py_vapid import Vapid02
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.database import SessionLocal
from app.deps import DbSession, Landlord
from app.encryption import EncryptionError, master_key, seal, unseal
from app.models import (
    BillStatus, LandlordProfile, Lease, PropertyBill, PropertyBillStatus, RentalBill,
    WebPushNotice, WebPushPreference, WebPushSubscription,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Web push"])
POLL_SECONDS = 60 * 60
LEASE_NOTICE_DAYS = 30
BILL_NOTICE_DAYS = 3
MAX_DEVICES = 10
# The server POSTs to client-supplied endpoints, so only known browser push services are accepted.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com")


def now():
    return datetime.now(timezone.utc)


def b64url_decode(value):
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return b""


@lru_cache
def vapid_for(private_key):
    try:
        vapid = Vapid02.from_string(private_key)
        public = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    except Exception:
        return None, None
    return vapid, base64.urlsafe_b64encode(public).decode().rstrip("=")


def vapid():
    return vapid_for(get_settings().web_push_vapid_private_key.get_secret_value())


def configured():
    contact = get_settings().web_push_contact
    return bool(vapid()[0] and (contact.startswith("mailto:") or contact.startswith("https://")))


def allowed_endpoint(value):
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        return (
            url.scheme == "https" and not url.username and not url.password and url.port in (None, 443)
            and (host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES))
        )
    except ValueError:
        return False


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def seal_subscription(landlord_id, subscription_id, info):
    return seal(master_key(), json.dumps(info).encode(), f"web-push:{landlord_id}:{subscription_id}")


def unseal_subscription(row):
    return json.loads(unseal(master_key(), row.subscription, f"web-push:{row.landlord_id}:{row.id}"))


def preference(db, landlord_id):
    return db.get(WebPushPreference, landlord_id) or WebPushPreference(
        landlord_id=landlord_id, lease_expiry=True, bill_due=True,
    )


def status_payload(db, landlord_id):
    prefs = preference(db, landlord_id)
    devices = db.scalar(select(func.count()).select_from(WebPushSubscription).where(
        WebPushSubscription.landlord_id == landlord_id,
    ))
    return {
        "available": configured(),
        "public_key": vapid()[1] if configured() else None,
        "devices": devices,
        "lease_expiry": prefs.lease_expiry,
        "bill_due": prefs.bill_due,
        "lease_notice_days": LEASE_NOTICE_DAYS,
        "bill_notice_days": BILL_NOTICE_DAYS,
    }


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_expiry: StrictBool
    bill_due: StrictBool


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(max_length=128)
    auth: str = Field(max_length=64)


class SubscriptionIn(BaseModel):
    # Matches PushSubscription.toJSON(); expirationTime is ignored.
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(max_length=2048)
    keys: SubscriptionKeys


class SubscriptionRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(max_length=2048)


def require_configured():
    if not configured():
        raise HTTPException(503, "Push notifications are not configured on this server.")


@router.get("/me/web-push")
def push_status(db: DbSession, landlord: Landlord, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return status_payload(db, landlord.id)


@router.put("/me/web-push")
def push_preferences(payload: Preferences, db: DbSession, landlord: Landlord, response: Response):
    response.headers["Cache-Control"] = "no-store"
    prefs = db.get(WebPushPreference, landlord.id) or WebPushPreference(landlord_id=landlord.id)
    prefs.lease_expiry = payload.lease_expiry
    prefs.bill_due = payload.bill_due
    db.add(prefs)
    db.commit()
    return status_payload(db, landlord.id)


@router.post("/me/web-push/subscriptions")
def push_subscribe(payload: SubscriptionIn, db: DbSession, landlord: Landlord, response: Response):
    response.headers["Cache-Control"] = "no-store"
    require_configured()
    if not allowed_endpoint(payload.endpoint):
        raise HTTPException(422, "This browser's push service is not supported.")
    if len(b64url_decode(payload.keys.p256dh)) != 65 or len(b64url_decode(payload.keys.auth)) != 16:
        raise HTTPException(422, "Invalid push subscription keys.")
    info = {"endpoint": payload.endpoint, "keys": {"p256dh": payload.keys.p256dh, "auth": payload.keys.auth}}
    # One browser profile has one endpoint; signing in as another landlord moves it.
    row = db.scalar(select(WebPushSubscription).where(WebPushSubscription.endpoint_hash == digest(payload.endpoint)))
    if row is None:
        count = db.scalar(select(func.count()).select_from(WebPushSubscription).where(
            WebPushSubscription.landlord_id == landlord.id,
        ))
        if count >= MAX_DEVICES:
            raise HTTPException(409, f"Turn off notifications on another device first (limit {MAX_DEVICES}).")
        row = WebPushSubscription(id=uuid.uuid4().hex, endpoint_hash=digest(payload.endpoint), created_at=now())
        db.add(row)
    row.landlord_id = landlord.id
    try:
        row.subscription = seal_subscription(landlord.id, row.id, info)
    except EncryptionError:
        raise HTTPException(503, "Configure the server encryption key before enabling notifications.") from None
    db.commit()
    return status_payload(db, landlord.id)


@router.delete("/me/web-push/subscriptions", status_code=204)
def push_unsubscribe(payload: SubscriptionRef, db: DbSession, landlord: Landlord):
    db.execute(delete(WebPushSubscription).where(
        WebPushSubscription.landlord_id == landlord.id,
        WebPushSubscription.endpoint_hash == digest(payload.endpoint),
    ))
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/me/web-push/test")
def push_test(db: DbSession, landlord: Landlord, response: Response):
    response.headers["Cache-Control"] = "no-store"
    require_configured()
    delivered = send_to_landlord(db, landlord.id, {
        "title": "Rental Management", "body": "Test notification: reminders will appear like this.", "tag": "test",
    })
    db.commit()
    if not delivered:
        raise HTTPException(502, "No device accepted the test notification. Turn notifications off and on again.")
    return status_payload(db, landlord.id)


def deliver(info, message):
    settings = get_settings()
    session = requests.Session()
    session.max_redirects = 0
    try:
        webpush(info, json.dumps(message), vapid_private_key=vapid()[0],
                vapid_claims={"sub": settings.web_push_contact}, ttl=24 * 60 * 60, timeout=10,
                requests_session=session)
    finally:
        session.close()


def send_to_landlord(db, landlord_id, message):
    """Returns True when at least one device accepted the message."""
    delivered = False
    rows = db.scalars(select(WebPushSubscription).where(WebPushSubscription.landlord_id == landlord_id))
    for row in list(rows):
        try:
            deliver(unseal_subscription(row), message)
        except EncryptionError:
            logger.error("Web push subscription could not be decrypted: subscription=%s", row.id)
            continue
        except WebPushException as exc:
            # 404/410: the browser dropped this subscription. Other errors retry next scan.
            if exc.response is not None and exc.response.status_code in (404, 410):
                db.delete(row)
            else:
                logger.error("Web push failed: subscription=%s status=%s", row.id,
                             exc.response.status_code if exc.response is not None else None)
            continue
        except requests.RequestException as exc:
            logger.error("Web push failed: subscription=%s error=%s", row.id, type(exc).__name__)
            continue
        row.last_success_at = now()
        delivered = True
    return delivered


def pending(db, kind, landlord_id, rows):
    """Rows of (id, due_date) that have not been announced yet."""
    rows = list(rows)
    if not rows:
        return []
    sent = set(db.execute(select(WebPushNotice.ref_id, WebPushNotice.due_date).where(
        WebPushNotice.landlord_id == landlord_id, WebPushNotice.kind == kind,
        WebPushNotice.ref_id.in_([ref for ref, _ in rows]),
    )).all())
    return [(ref, due) for ref, due in rows if (ref, due) not in sent]


def day(value):
    return f"{value:%b} {value.day}"


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def lease_message(leases):
    first = min(due for _, due in leases)
    if len(leases) == 1:
        body = f"A lease ends on {day(first)}. Plan a renewal or move-out."
    else:
        body = f"{len(leases)} leases end in the next {LEASE_NOTICE_DAYS} days, the first on {day(first)}."
    return {"title": "Lease ending soon", "body": body, "tag": "lease-expiry"}


def bill_message(property_bills, tenant_bills, today):
    parts = []
    if property_bills:
        parts.append(f"{plural(len(property_bills), 'property bill')} to pay")
    if tenant_bills:
        parts.append(f"{plural(len(tenant_bills), 'tenant bill')} unpaid")
    first = min(due for _, due in property_bills + tenant_bills)
    when = "today" if first == today else f"from {day(first)}"
    return {"title": "Bills due soon", "body": f"{' and '.join(parts)}, due {when}.", "tag": "bill-due"}


def notify_landlord(db, landlord_id, today):
    landlord = db.get(LandlordProfile, landlord_id)
    if not landlord or not landlord.user.is_active or landlord.user.role != "landlord":
        return
    prefs = preference(db, landlord_id)
    batches = []
    if prefs.lease_expiry:
        leases = pending(db, "lease", landlord_id, db.execute(select(Lease.id, Lease.end_date).where(
            Lease.landlord_id == landlord_id, Lease.end_date >= today,
            Lease.end_date <= today + timedelta(days=LEASE_NOTICE_DAYS),
        )).all())
        if leases:
            batches.append((lease_message(leases), [("lease", leases)]))
    if prefs.bill_due:
        horizon = today + timedelta(days=BILL_NOTICE_DAYS)
        property_bills = pending(db, "property_bill", landlord_id, db.execute(
            select(PropertyBill.id, PropertyBill.due_date).where(
                PropertyBill.landlord_id == landlord_id, PropertyBill.status == PropertyBillStatus.unpaid.value,
                PropertyBill.due_date >= today, PropertyBill.due_date <= horizon,
            )).all())
        tenant_bills = pending(db, "tenant_bill", landlord_id, db.execute(
            select(RentalBill.id, RentalBill.due_date).where(
                RentalBill.landlord_id == landlord_id,
                RentalBill.status.in_([BillStatus.open.value, BillStatus.partial.value]),
                RentalBill.due_date >= today, RentalBill.due_date <= horizon,
            )).all())
        if property_bills or tenant_bills:
            batches.append((bill_message(property_bills, tenant_bills, today),
                            [("property_bill", property_bills), ("tenant_bill", tenant_bills)]))
    for message, groups in batches:
        # Nothing is marked sent unless a device accepted it, so failures retry next scan.
        if not send_to_landlord(db, landlord_id, message):
            return
        for kind, rows in groups:
            db.add_all(WebPushNotice(kind=kind, ref_id=ref, due_date=due, landlord_id=landlord_id, sent_at=now())
                       for ref, due in rows)


def reconcile_push_notifications(session_factory=SessionLocal, today=None):
    if not configured():
        return
    today = today or date.today()
    with session_factory() as db:
        db.execute(delete(WebPushNotice).where(WebPushNotice.due_date < today - timedelta(days=30)))
        db.commit()
        landlord_ids = list(db.scalars(select(WebPushSubscription.landlord_id).distinct()))
    for landlord_id in landlord_ids:
        try:
            with session_factory() as db:
                notify_landlord(db, landlord_id, today)
                db.commit()
        except Exception as exc:
            # No subscription endpoints, payloads, or exception messages in logs.
            logger.error("Web push reminders failed: landlord=%s error=%s", landlord_id, type(exc).__name__)


async def push_notification_scheduler():
    while True:
        try:
            await asyncio.to_thread(reconcile_push_notifications)
        except Exception as exc:
            logger.error("Web push scan failed: error=%s", type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)
