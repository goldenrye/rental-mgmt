"""Calendar tests use an isolated database and fake Google responses only."""

import asyncio
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import google_calendar as calendar
from app.billing import generate_bills_for_lease
from app.config import get_settings
from app.database import Base, get_db
from app.encryption import EncryptionError, encode
from app.main import app
from app.models import (
    GoogleCalendarConnection, GoogleCalendarExport, LandlordProfile, Lease, LeaseTenant,
    RentalBill, RentalProperty, TenantProfile, User, UserAccess,
)
from app.security import create_access_token


class GoogleCalendarTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{directory.name}/calendar.db", connect_args={"timeout": 20})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        for key, value in {
            "jwt_secret_key": "calendar-test-only-secret-0123456789abcdef",
            "data_encryption_master_key": SecretStr(encode(os.urandom(32))),
            "google_calendar_client_id": "test-client",
            "google_calendar_client_secret": SecretStr("test-client-secret"),
            "google_calendar_redirect_uri": "http://localhost:8000/google-calendar/callback",
            "google_calendar_app_url": "http://localhost:5173",
        }.items():
            patcher = patch.object(get_settings(), key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.owners = []
        with self.sessions() as db:
            db.add(User(email="other-role@example.test", full_name="Tenant", role="tenant", hashed_password="unused"))
            db.flush()
            self.tenant_token = create_access_token("1")
            for index in range(2):
                owner = LandlordProfile(encryption_enabled=bool(index), user=User(
                    email=f"calendar{index}@example.test", full_name=f"Owner {index}",
                    hashed_password="unused", role="landlord",
                ))
                property_ = RentalProperty(landlord=owner, name=f"Home {index}", address="Private address")
                tenant = TenantProfile(landlord=owner, full_name=f"Tenant {index}")
                db.add_all([property_, tenant])
                db.flush()
                db.add(UserAccess(user_id=owner.user_id))
                lease = Lease(landlord_id=owner.id, property=property_,
                              start_date=date(2026, 1, 1), end_date=date(2027, 12, 31), monthly_rent=310)
                lease.tenants = [LeaseTenant(tenant=tenant, monthly_rent=310, deposit=0)]
                db.add(lease)
                db.flush()
                self.owners.append((owner.id, owner.user_id, lease.id, create_access_token(str(owner.user_id))))
            db.commit()

        def test_db():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.google_patch = patch.object(calendar, "GoogleClient")
        self.google_factory = self.google_patch.start()
        self.addCleanup(self.google_patch.stop)
        self.google = self.google_factory.return_value.__enter__.return_value
        self.google.token.return_value = {
            "access_token": "private-access-token", "refresh_token": "private-refresh-token",
            "expires_in": 3600, "scope": calendar.SCOPE,
        }
        self.google.calendar.return_value = "rental-calendar@group.calendar.google.com"

    def headers(self, owner=0):
        return {"Authorization": f"Bearer {self.owners[owner][3]}"}

    def status(self, owner=0):
        response = self.client.get("/me/google-calendar", headers=self.headers(owner))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def start(self, owner=0):
        response = self.client.post("/me/google-calendar/connect", headers=self.headers(owner))
        self.assertEqual(response.status_code, 200, response.text)
        params = parse_qs(urlsplit(response.json()["authorization_url"]).query)
        self.assertEqual(params["scope"], [calendar.SCOPE])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        return params["state"][0]

    def connect(self, owner=0):
        state = self.start(owner)
        response = self.client.get("/google-calendar/callback", params={"state": state, "code": "test-code"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("still off", response.text)
        self.assertFalse(self.status(owner)["enabled"])
        return state

    def enable(self, owner=0):
        response = self.client.put("/me/google-calendar", headers=self.headers(owner), json={"enabled": True})
        self.assertEqual(response.status_code, 200, response.text)

    def generate(self, owner=0, month=10):
        with self.sessions() as db:
            bills = generate_bills_for_lease(db, db.get(Lease, self.owners[owner][2]), date(2026, month, 1))
            db.commit()
            return bills[0].id

    def jobs(self):
        with self.sessions() as db:
            return [(j.id, j.status, j.attempts) for j in db.scalars(select(GoogleCalendarExport))]

    def test_default_off_no_exports_and_no_backfill_after_enabling(self):
        self.assertFalse(self.status()["enabled"])
        self.assertFalse(self.status()["connected"])
        self.generate()
        self.assertEqual(self.jobs(), [])
        self.google_factory.assert_not_called()
        self.connect()
        self.generate(month=9)
        self.assertEqual(self.jobs(), [])
        self.enable()
        self.generate()  # Existing bill is never queued retroactively.
        self.assertEqual(self.jobs(), [])
        self.generate(month=11)
        self.generate(month=11)
        self.assertEqual(len(self.jobs()), 1)

    def test_settings_require_landlord_and_ignore_cross_owner_payloads(self):
        self.assertEqual(self.client.get("/me/google-calendar").status_code, 401)
        for method, path, payload in [
            ("get", "/me/google-calendar", None),
            ("put", "/me/google-calendar", {"enabled": True}),
            ("post", "/me/google-calendar/connect", None),
            ("delete", "/me/google-calendar", None),
        ]:
            kwargs = {"headers": {"Authorization": f"Bearer {self.tenant_token}"}}
            if payload is not None:
                kwargs["json"] = payload
            self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 403)
        response = self.client.put("/me/google-calendar", headers=self.headers(), json={"enabled": True})
        self.assertEqual(response.status_code, 409)
        self.connect()
        self.enable()
        self.assertFalse(self.status(1)["enabled"])
        self.assertFalse(self.status(1)["connected"])
        self.assertEqual(self.client.put("/me/google-calendar", headers=self.headers(),
                                        json={"enabled": True, "landlord_id": 2}).status_code, 422)
        self.assertEqual(self.client.put("/me/google-calendar", headers=self.headers(),
                                        json={"enabled": "false"}).status_code, 422)
        self.client.delete("/me/google-calendar", headers=self.headers(1))
        self.assertTrue(self.status()["enabled"])

    def test_missing_configuration_stays_off_and_can_still_disable(self):
        with patch.object(get_settings(), "google_calendar_client_id", ""):
            self.assertFalse(self.status()["available"])
            self.assertEqual(self.client.post("/me/google-calendar/connect", headers=self.headers()).status_code, 503)
            self.assertEqual(self.client.put("/me/google-calendar", headers=self.headers(), json={"enabled": False}).status_code, 200)
        for bad in ("http://example.com", "https://user:secret@example.com", "https://example.com/#token"):
            with patch.object(get_settings(), "google_calendar_redirect_uri", bad):
                self.assertFalse(calendar.configured())

    def test_missing_encryption_key_gives_safe_setup_error(self):
        with patch.object(calendar, "master_key", side_effect=EncryptionError("private-key-details")):
            response = self.client.post("/me/google-calendar/connect", headers=self.headers())
        self.assertEqual(response.status_code, 503)
        self.assertIn("encryption key", response.json()["detail"])
        self.assertNotIn("private-key-details", response.text)
        self.assertFalse(self.status()["connected"])
        state = self.start()
        with patch.object(calendar, "master_key", side_effect=EncryptionError("private-key-details")):
            response = self.client.get("/google-calendar/callback", params={"state": state, "code": "test-code"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("private-key-details", response.text)
        self.google.token.assert_not_called()

    def test_oauth_is_browser_bound_single_use_and_tokens_are_encrypted(self):
        state = self.start()
        other = TestClient(app)
        self.addCleanup(other.close)
        response = other.get("/google-calendar/callback", params={"state": state, "code": "stolen"})
        self.assertEqual(response.status_code, 400)
        self.google.token.assert_not_called()
        response = self.client.get("/google-calendar/callback", params={"state": state, "code": "test-code"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/google-calendar/callback", params={"state": state, "code": "replay"}).status_code, 400)
        self.assertEqual(self.google.token.call_count, 1)
        self.assertNotIn("private-access-token", str(self.status()))
        with self.sessions() as db:
            connection = db.get(GoogleCalendarConnection, self.owners[0][0])
            self.assertTrue(connection.credentials.startswith("v1:"))
            self.assertIsNone(connection.oauth_state_hash)
            dump = "\n".join(db.connection().connection.driver_connection.iterdump())
            self.assertNotIn("private-refresh-token", dump)
            self.assertNotIn("private-access-token", dump)
            self.assertNotIn("test-client-secret", dump)
            with self.assertRaises(EncryptionError):
                calendar.decrypt_secret(self.owners[1][0], "credentials", connection.credentials)

    def test_expired_replaced_cancelled_and_revoked_oauth_requests(self):
        first = self.start()
        second = self.start()
        self.assertEqual(self.client.get("/google-calendar/callback", params={"state": first, "code": "code"}).status_code, 400)
        second = self.start()
        response = self.client.get("/google-calendar/callback", params={"state": second, "error": "access_denied"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.status()["connected"])
        for change in ("expired", "revoked", "inactive"):
            state = self.start()
            with self.sessions() as db:
                connection = db.get(GoogleCalendarConnection, self.owners[0][0])
                access = db.get(UserAccess, self.owners[0][1])
                if change == "expired":
                    connection.oauth_expires_at = calendar.now() - timedelta(minutes=1)
                elif change == "revoked":
                    access.token_version += 1
                else:
                    access.is_active = False
                db.commit()
            self.assertEqual(self.client.get("/google-calendar/callback", params={"state": state, "code": "code"}).status_code, 400)
            with self.sessions() as db:
                access = db.get(UserAccess, self.owners[0][1])
                access.token_version = 0
                access.is_active = True
                db.commit()
        self.google.token.assert_not_called()

    def test_no_refresh_token_does_not_connect_or_enable(self):
        self.google.token.return_value = {"access_token": "temporary-token"}
        state = self.start()
        response = self.client.get("/google-calendar/callback", params={"state": state, "code": "code"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.status()["connected"])
        self.assertFalse(self.status()["enabled"])

    def test_encrypted_owner_exports_due_date_once_with_no_invites(self):
        self.connect(1)
        self.enable(1)
        self.generate(1)
        self.generate(0)
        calendar.reconcile_calendar_exports(self.sessions)
        calendar.reconcile_calendar_exports(self.sessions)
        self.google.insert_event.assert_called_once()
        token, calendar_id, event = self.google.insert_event.call_args.args
        self.assertEqual(token, "private-access-token")
        self.assertEqual(calendar_id, self.google.calendar.return_value)
        self.assertEqual(event["start"], {"date": "2026-10-01"})
        self.assertEqual(event["end"], {"date": "2026-10-02"})
        self.assertIn("Tenant 1", event["summary"])
        self.assertIn("Home 1", event["summary"])
        self.assertIn("$310.00", event["description"])
        self.assertNotIn("Tenant 0", str(event))
        self.assertNotIn("attendees", event)
        self.assertEqual(event["visibility"], "private")
        self.assertEqual(event["reminders"], {"useDefault": False})
        self.assertRegex(event["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.jobs()[0][1], "sent")
        self.assertIsNotNone(self.status(1)["last_synced_at"])

    def test_delivery_retries_with_same_event_id_and_backoff(self):
        self.connect()
        self.enable()
        self.generate()
        self.google.insert_event.side_effect = [calendar.GoogleFailure(), None]
        calendar.reconcile_calendar_exports(self.sessions)
        self.assertEqual(self.jobs()[0][1:], ("pending", 1))
        self.assertIsNotNone(self.status()["last_error"])
        calendar.reconcile_calendar_exports(self.sessions)
        self.assertEqual(self.google.insert_event.call_count, 1)
        with self.sessions() as db:
            job = db.scalar(select(GoogleCalendarExport))
            job.next_attempt_at = calendar.now() - timedelta(seconds=1)
            db.commit()
        calendar.reconcile_calendar_exports(self.sessions)
        calls = self.google.insert_event.call_args_list
        self.assertEqual(calls[0].args[2]["id"], calls[1].args[2]["id"])
        self.assertEqual(self.jobs()[0][1], "sent")
        self.assertIsNone(self.status()["last_error"])

    def test_disable_cancels_pending_and_reenable_does_not_backfill(self):
        self.connect()
        self.enable()
        self.generate()
        response = self.client.put("/me/google-calendar", headers=self.headers(), json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.jobs()[0][1], "skipped")
        self.generate(month=11)
        self.enable()
        self.generate(month=11)
        calendar.reconcile_calendar_exports(self.sessions)
        self.google.insert_event.assert_not_called()
        self.generate(month=12)
        calendar.reconcile_calendar_exports(self.sessions)
        self.google.insert_event.assert_called_once()
        self.assertEqual(self.client.delete("/me/google-calendar", headers=self.headers()).status_code, 204)
        self.assertFalse(self.status()["connected"])
        with self.sessions() as db:
            self.assertIsNone(db.get(GoogleCalendarConnection, self.owners[0][0]).credentials)
        self.assertEqual(self.jobs()[-1][1], "sent")

    def test_bill_rollback_and_deletion_remove_pending_exports(self):
        self.connect()
        self.enable()
        with self.sessions() as db:
            generate_bills_for_lease(db, db.get(Lease, self.owners[0][2]), date(2026, 10, 1))
            db.flush()
            self.assertEqual(db.scalar(select(func.count()).select_from(GoogleCalendarExport)), 1)
            db.rollback()
        self.assertEqual(self.jobs(), [])
        bill_id = self.generate()
        with self.sessions() as db:
            db.delete(db.get(RentalBill, bill_id))
            db.commit()
        self.assertEqual(self.jobs(), [])
        calendar.reconcile_calendar_exports(self.sessions)
        self.google.insert_event.assert_not_called()

    def test_other_bill_types_are_never_enqueued(self):
        self.connect()
        self.enable()
        with self.sessions() as db:
            calendar.enqueue_rent_exports(db, [
                SimpleNamespace(bill_type="manual", source_property_bill_id=None),
                SimpleNamespace(bill_type="water", source_property_bill_id=1),
                SimpleNamespace(bill_type="rent", source_property_bill_id=1),
            ])
            db.commit()
        self.assertEqual(self.jobs(), [])

    def test_access_refresh_and_revoked_authorization(self):
        self.connect()
        self.enable()
        with self.sessions() as db:
            connection = db.get(GoogleCalendarConnection, self.owners[0][0])
            credentials = calendar.decrypt_secret(connection.landlord_id, "credentials", connection.credentials)
            credentials["expires_at"] = 0
            connection.credentials = calendar.encrypt_secret(connection.landlord_id, "credentials", credentials)
            db.commit()
        self.generate()
        calendar.reconcile_calendar_exports(self.sessions)
        self.google.token.assert_called_with(grant_type="refresh_token", refresh_token="private-refresh-token")
        self.generate(month=11)
        self.google.insert_event.side_effect = calendar.GoogleFailure(reconnect=True)
        calendar.reconcile_calendar_exports(self.sessions)
        self.assertFalse(self.status()["enabled"])
        self.assertFalse(self.status()["connected"])
        self.assertEqual(self.jobs()[-1][1], "skipped")

    def test_deactivated_account_does_not_export(self):
        self.connect()
        self.enable()
        self.generate()
        with self.sessions() as db:
            db.get(UserAccess, self.owners[0][1]).is_active = False
            db.commit()
        calendar.reconcile_calendar_exports(self.sessions)
        self.google.insert_event.assert_not_called()
        self.assertEqual(self.jobs()[0][1], "skipped")

    def test_concurrent_workers_deliver_once(self):
        self.connect()
        self.enable()
        self.generate()
        job_id = self.jobs()[0][0]
        def deliver():
            with self.sessions() as db:
                calendar.deliver_export(db, job_id)
                db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(deliver) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        self.google.insert_event.assert_called_once()

    def test_loop_survives_scan_failure(self):
        async def run():
            with patch.object(calendar.asyncio, "to_thread", new=AsyncMock(side_effect=[RuntimeError(), None])) as worker:
                with patch.object(calendar.asyncio, "sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])):
                    with self.assertLogs(calendar.logger, level="ERROR"):
                        with self.assertRaises(asyncio.CancelledError):
                            await calendar.calendar_export_scheduler()
                    self.assertEqual(worker.await_count, 2)
        asyncio.run(run())


class GoogleClientTests(unittest.TestCase):
    def client(self, handler):
        client = calendar.GoogleClient()
        client.http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.http.close)
        return client

    def test_event_conflicts_are_idempotent_and_notifications_disabled(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(409)
        client = self.client(handler)
        client.insert_event("access", "calendar@example.com", {"id": "abc123"})
        self.assertEqual(requests[0].url.params["sendUpdates"], "none")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer access")
        self.assertIn("calendar%40example.com", str(requests[0].url))

    def test_expired_grants_and_missing_scope_are_rejected_without_leaking_details(self):
        client = self.client(lambda request: httpx.Response(400, json={"error": "invalid_grant", "detail": "private-token"}))
        with self.assertRaises(calendar.GoogleFailure) as caught:
            client.token(grant_type="refresh_token")
        self.assertTrue(caught.exception.reconnect)
        self.assertNotIn("private-token", str(caught.exception))
        client = self.client(lambda request: httpx.Response(200, json={"access_token": "token", "scope": "unrelated"}))
        with self.assertRaises(calendar.GoogleFailure):
            client.token(grant_type="authorization_code")


if __name__ == "__main__":
    unittest.main()
