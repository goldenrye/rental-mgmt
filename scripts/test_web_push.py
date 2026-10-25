"""Web push tests use an isolated database and never contact a real push service."""

import base64
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import http_ece
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pywebpush import WebPushException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import web_push
from app.config import get_settings
from app.database import Base, get_db
from app.encryption import encode
from app.main import app
from app.models import (
    LandlordProfile, Lease, LeaseTenant, PropertyBill, RentalBill, RentalProperty, TenantProfile, User,
    UserAccess, WebPushNotice, WebPushSubscription,
)
from app.security import create_access_token

TODAY = date(2026, 10, 25)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def browser_keys():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return private, os.urandom(16), {"p256dh": b64(public)}


def subscription(name="device", keys=None):
    if keys is None:
        _, auth, keys = browser_keys()
        keys = {**keys, "auth": b64(auth)}
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{name}", "expirationTime": None, "keys": keys}


class WebPushTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{directory.name}/push.db")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        vapid_key = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")
        for key, value in {
            "jwt_secret_key": "push-test-only-secret-0123456789abcdef",
            "data_encryption_master_key": SecretStr(encode(os.urandom(32))),
            "web_push_vapid_private_key": SecretStr(b64(vapid_key)),
            "web_push_contact": "mailto:admin@example.test",
        }.items():
            patcher = patch.object(get_settings(), key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.owners = []
        with self.sessions() as db:
            for index in range(2):
                owner = LandlordProfile(user=User(
                    email=f"push{index}@example.test", full_name=f"Owner {index}",
                    hashed_password="unused", role="landlord",
                ))
                property_ = RentalProperty(landlord=owner, name=f"Secret Street {index}", address="Private")
                tenant = TenantProfile(landlord=owner, full_name=f"Private Tenant {index}")
                db.add_all([property_, tenant])
                db.flush()
                db.add(UserAccess(user_id=owner.user_id))
                lease = Lease(landlord_id=owner.id, property=property_, start_date=date(2025, 11, 15),
                              end_date=TODAY + timedelta(days=20), monthly_rent=1200)
                lease.tenants = [LeaseTenant(tenant=tenant, monthly_rent=1200, deposit=0)]
                db.add(lease)
                db.flush()
                db.add_all([
                    PropertyBill(landlord_id=owner.id, property_id=property_.id, bill_type="water", title="Water",
                                 due_date=TODAY + timedelta(days=2), amount_due=80),
                    PropertyBill(landlord_id=owner.id, property_id=property_.id, bill_type="water", title="Later",
                                 due_date=TODAY + timedelta(days=10), amount_due=80),
                    PropertyBill(landlord_id=owner.id, property_id=property_.id, bill_type="water", title="Paid",
                                 due_date=TODAY, amount_due=80, status="paid"),
                    RentalBill(landlord_id=owner.id, lease_id=lease.id, tenant_id=tenant.id, bill_year=2026,
                               bill_month=10, due_date=TODAY + timedelta(days=1), amount_due=1200, balance=1200),
                ])
                self.owners.append((owner.id, create_access_token(str(owner.user_id))))
            db.commit()

        def test_db():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.sent = []
        self.deliver_patch = patch.object(web_push, "deliver", side_effect=lambda info, message: self.sent.append((info, message)))
        self.deliver = self.deliver_patch.start()
        self.addCleanup(self.deliver_patch.stop)

    def headers(self, owner=0):
        return {"Authorization": f"Bearer {self.owners[owner][1]}"}

    def subscribe(self, owner=0, body=None):
        response = self.client.post("/me/web-push/subscriptions", headers=self.headers(owner),
                                    json=body or subscription(f"owner{owner}"))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def reconcile(self, today=TODAY):
        web_push.reconcile_push_notifications(self.sessions, today)

    def test_status_reports_public_key_and_unconfigured_server(self):
        status = self.client.get("/me/web-push", headers=self.headers()).json()
        self.assertTrue(status["available"])
        self.assertEqual(len(base64.urlsafe_b64decode(status["public_key"] + "=")), 65)
        self.assertEqual((status["devices"], status["lease_expiry"], status["bill_due"]), (0, True, True))
        with patch.object(get_settings(), "web_push_contact", ""):
            self.assertFalse(self.client.get("/me/web-push", headers=self.headers()).json()["available"])
            response = self.client.post("/me/web-push/subscriptions", headers=self.headers(), json=subscription())
            self.assertEqual(response.status_code, 503)
            self.reconcile()
        self.deliver.assert_not_called()

    def test_rejects_unknown_push_hosts_and_bad_keys(self):
        for endpoint in ("http://fcm.googleapis.com/x", "https://169.254.169.254/latest",
                         "https://fcm.googleapis.com.evil.test/x", "https://user@fcm.googleapis.com/x",
                         "https://fcm.googleapis.com:8443/x"):
            body = {**subscription(), "endpoint": endpoint}
            response = self.client.post("/me/web-push/subscriptions", headers=self.headers(), json=body)
            self.assertEqual(response.status_code, 422, endpoint)
        body = subscription()
        body["keys"]["auth"] = b64(b"short")
        self.assertEqual(self.client.post("/me/web-push/subscriptions", headers=self.headers(), json=body).status_code, 422)
        self.assertEqual(self.client.get("/me/web-push").status_code, 401)

    def test_subscription_is_sealed_and_moves_with_sign_in(self):
        body = subscription("shared-browser")
        self.assertEqual(self.subscribe(0, body)["devices"], 1)
        with self.sessions() as db:
            row = db.scalar(select(WebPushSubscription))
            self.assertNotIn("shared-browser", row.subscription)
            self.assertEqual(web_push.unseal_subscription(row)["endpoint"], body["endpoint"])
        self.assertEqual(self.subscribe(1, body)["devices"], 1)
        self.assertEqual(self.client.get("/me/web-push", headers=self.headers(0)).json()["devices"], 0)
        # Only the current owner can remove it.
        self.client.request("DELETE", "/me/web-push/subscriptions", headers=self.headers(0), json={"endpoint": body["endpoint"]})
        self.assertEqual(self.client.get("/me/web-push", headers=self.headers(1)).json()["devices"], 1)
        response = self.client.request("DELETE", "/me/web-push/subscriptions", headers=self.headers(1),
                                       json={"endpoint": body["endpoint"]})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/me/web-push", headers=self.headers(1)).json()["devices"], 0)

    def test_reminders_are_sent_once_without_private_details(self):
        self.subscribe(0)
        self.reconcile()
        messages = [message for _, message in self.sent]
        self.assertEqual(messages, [
            {"title": "Lease ending soon", "body": "A lease ends on Nov 14. Plan a renewal or move-out.", "tag": "lease-expiry"},
            {"title": "Bills due soon", "body": "1 property bill to pay and 1 tenant bill unpaid, due from Oct 26.", "tag": "bill-due"},
        ])
        self.assertNotIn("Private", json.dumps(messages))
        self.assertNotIn("Secret", json.dumps(messages))
        self.sent.clear()
        self.reconcile()
        self.reconcile(TODAY + timedelta(days=1))
        self.assertEqual(self.sent, [])
        # The later property bill enters the window on its own and is announced alone.
        self.reconcile(TODAY + timedelta(days=7))
        self.assertEqual([m["body"] for _, m in self.sent], ["1 property bill to pay, due from Nov 4."])

    def test_landlords_only_receive_their_own_reminders(self):
        self.subscribe(1)
        self.reconcile()
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(info["endpoint"].endswith("/owner1") for info, _ in self.sent))
        with self.sessions() as db:
            self.assertEqual({n.landlord_id for n in db.scalars(select(WebPushNotice))}, {self.owners[1][0]})

    def test_preferences_disable_each_reminder_type(self):
        self.subscribe(0)
        response = self.client.put("/me/web-push", headers=self.headers(), json={"lease_expiry": False, "bill_due": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.reconcile()
        self.assertEqual([m["tag"] for _, m in self.sent], ["bill-due"])
        self.client.put("/me/web-push", headers=self.headers(), json={"lease_expiry": True, "bill_due": False})
        self.sent.clear()
        self.reconcile()
        self.assertEqual([m["tag"] for _, m in self.sent], ["lease-expiry"])

    def test_failed_delivery_retries_and_gone_subscriptions_are_removed(self):
        self.subscribe(0)
        self.deliver.side_effect = requests.ConnectionError()
        self.reconcile()
        with self.sessions() as db:
            self.assertEqual(list(db.scalars(select(WebPushNotice))), [])
        self.deliver.side_effect = WebPushException("gone", response=type("R", (), {"status_code": 410})())
        self.reconcile()
        with self.sessions() as db:
            self.assertEqual(list(db.scalars(select(WebPushSubscription))), [])
            self.assertEqual(list(db.scalars(select(WebPushNotice))), [])

    def test_inactive_landlords_are_skipped(self):
        self.subscribe(0)
        with self.sessions() as db:
            owner = db.get(LandlordProfile, self.owners[0][0])
            db.get(UserAccess, owner.user_id).is_active = False
            db.commit()
        self.reconcile()
        self.deliver.assert_not_called()

    def test_test_notification(self):
        response = self.client.post("/me/web-push/test", headers=self.headers())
        self.assertEqual(response.status_code, 502)
        self.subscribe(0)
        response = self.client.post("/me/web-push/test", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.sent[0][1]["tag"], "test")

    def test_real_delivery_is_encrypted_for_the_browser_and_vapid_signed(self):
        self.deliver_patch.stop()
        private, auth, keys = browser_keys()
        self.subscribe(0, subscription("real", {**keys, "auth": b64(auth)}))
        posted = []

        def post(session, url, **kwargs):
            posted.append((session.max_redirects, url, kwargs))
            return type("R", (), {"status_code": 201, "text": "", "headers": {}})()
        with patch.object(requests.Session, "post", post):
            response = self.client.post("/me/web-push/test", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        max_redirects, url, kwargs = posted[0]
        self.assertEqual((max_redirects, url), (0, "https://fcm.googleapis.com/fcm/send/real"))
        self.assertTrue(kwargs["headers"]["authorization"].startswith("vapid t="))
        plaintext = http_ece.decrypt(kwargs["data"], private_key=private, auth_secret=auth, version="aes128gcm")
        self.assertEqual(json.loads(plaintext)["tag"], "test")


if __name__ == "__main__":
    unittest.main()
