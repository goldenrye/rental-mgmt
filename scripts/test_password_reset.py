"""Self-service password reset tests against an isolated database, with email sending mocked."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.account_access import bootstrap_super_admin, ensure_access
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import LandlordProfile, PasswordResetToken, User
from app.password_reset import MAX_REQUESTS_PER_HOUR, REQUEST_ACCEPTED, hash_code
from app.security import hash_password


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        settings = get_settings()
        patch.object(settings, "jwt_secret_key", "test-only-reset-secret-0123456789abcdef").start()
        patch.object(settings, "super_admin_initial_password", "initial-admin-password").start()
        patch.object(settings, "smtp_host", "smtp.example.com").start()
        patch.object(settings, "smtp_from", "no-reply@example.com").start()
        patch.object(settings, "app_url", "https://rent.example.com/").start()
        self.sent = []
        patch("app.password_reset.send_email", side_effect=lambda to, subject, body: self.sent.append((to, body))).start()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        with self.sessions() as db:
            bootstrap_super_admin(db)
            owner = User(email="owner@example.com", full_name="Owner", role="landlord", hashed_password=hash_password("old-password"))
            db.add(owner)
            db.flush()
            db.add(LandlordProfile(user_id=owner.id))
            db.commit()
            self.owner_id = owner.id

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def login(self, email, password):
        return self.client.post("/auth/login", json={"email": email, "password": password})

    def request_reset(self, email="owner@example.com"):
        response = self.client.post("/auth/password-reset/request", json={"email": email})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["detail"], REQUEST_ACCEPTED)
        return response

    def emailed_code(self):
        to, body = self.sent[-1]
        self.assertEqual(to, "owner@example.com")
        link = next(line for line in body.splitlines() if "#reset=" in line)
        self.assertTrue(link.startswith("https://rent.example.com/#reset="))
        return link.split("#reset=", 1)[1]

    def confirm(self, code, password="new-password"):
        return self.client.post("/auth/password-reset/confirm", json={"token": code, "new_password": password})

    def test_reset_link_sets_password_once_and_ends_sessions(self):
        session = self.login("owner@example.com", "old-password").json()["access_token"]
        self.request_reset("Owner@Example.com")
        code = self.emailed_code()
        with self.sessions() as db:
            stored = db.scalar(select(PasswordResetToken))
            self.assertEqual(stored.code_hash, hash_code(code))
            self.assertNotIn(code, stored.code_hash)

        self.assertEqual(self.confirm(code).status_code, 204)
        self.assertEqual(self.login("owner@example.com", "old-password").status_code, 401)
        self.assertEqual(self.login("owner@example.com", "new-password").status_code, 200)
        self.assertEqual(self.client.get("/me", headers={"Authorization": f"Bearer {session}"}).status_code, 401)
        self.assertEqual(self.confirm(code, "another-password").status_code, 400)

    def test_unknown_admin_and_deactivated_accounts_get_the_same_reply_and_no_email(self):
        self.request_reset("nobody@example.com")
        with self.sessions() as db:
            admin = db.scalar(select(User).where(User.email == "admin"))
            admin.email = "admin@example.com"
            ensure_access(db, db.get(User, self.owner_id)).is_active = False
            db.commit()
        self.request_reset("admin@example.com")
        self.request_reset("owner@example.com")
        self.assertEqual(self.sent, [])

    def test_expired_links_are_rejected_and_a_reset_voids_other_links(self):
        self.request_reset()
        first = self.emailed_code()
        self.request_reset()
        second = self.emailed_code()
        with self.sessions() as db:
            token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.code_hash == hash_code(first)))
            token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        self.assertEqual(self.confirm(first).status_code, 400)
        self.assertEqual(self.confirm(second).status_code, 204)
        self.request_reset()
        third = self.emailed_code()
        self.assertEqual(self.confirm(third, "third-password").status_code, 204)
        # Using one link invalidates every other outstanding link for the account.
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(PasswordResetToken).where(PasswordResetToken.used_at.is_(None))), None)

    def test_requests_per_account_are_capped(self):
        for _ in range(MAX_REQUESTS_PER_HOUR + 2):
            self.request_reset()
        self.assertEqual(len(self.sent), MAX_REQUESTS_PER_HOUR)

    def test_unconfigured_server_says_reset_is_unavailable(self):
        patch.object(get_settings(), "smtp_host", "").start()
        response = self.client.post("/auth/password-reset/request", json={"email": "owner@example.com"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.sent, [])

    def test_short_password_and_unknown_code_are_rejected(self):
        self.request_reset()
        self.assertEqual(self.confirm(self.emailed_code(), "short").status_code, 422)
        self.assertEqual(self.confirm("x" * 40).status_code, 400)


if __name__ == "__main__":
    unittest.main()
