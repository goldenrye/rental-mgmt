"""Initial admin password, login lockout, and tenant-account claim tests against an isolated database."""

import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.account_access import bootstrap_super_admin
from app.config import get_settings
from app.database import Base, get_db
from app.login_throttle import MAX_FAILURES
from app.main import app
from app.models import LandlordProfile, LoginThrottle, TenantProfile, User, UserAccess
from app.security import hash_password, verify_password


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(get_settings(), "jwt_secret_key", "test-only-hardening-secret-0123456789abcdef").start()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        with self.sessions() as db:
            owner = User(email="owner@example.com", full_name="Owner", role="landlord", hashed_password=hash_password("owner-password"))
            tenant = User(email="private-tenant@example.com", full_name="Tenant", role="tenant", hashed_password=owner.hashed_password)
            db.add_all([owner, tenant])
            db.flush()
            db.add_all([LandlordProfile(user_id=owner.id), TenantProfile(user_id=tenant.id, full_name="Tenant", email=tenant.email)])
            db.commit()
            self.tenant_user_id = tenant.id

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def bootstrap(self, configured):
        patch.object(get_settings(), "super_admin_initial_password", configured).start()
        output = io.StringIO()
        with redirect_stdout(output), self.sessions() as db:
            bootstrap_super_admin(db)
        return output.getvalue()

    def admin_hash(self):
        with self.sessions() as db:
            return db.scalar(select(User.hashed_password).where(User.email == "admin"))

    def login(self, email, password):
        return self.client.post("/auth/login", json={"email": email, "password": password})

    def test_weak_or_unset_initial_admin_password_is_replaced_with_random(self):
        for configured in ("", "admin", "short-pass"):
            with self.subTest(configured=configured):
                with self.sessions() as db:
                    db.query(UserAccess).delete()
                    db.query(User).filter(User.email == "admin").delete()
                    db.commit()
                output = self.bootstrap(configured)
                self.assertFalse(verify_password("admin", self.admin_hash()))
                generated = output.split("'admin' account: ")[1].split()[0]
                self.assertTrue(verify_password(generated, self.admin_hash()))

    def test_strong_initial_admin_password_is_used_without_printing(self):
        output = self.bootstrap("a-strong-initial-password")
        self.assertEqual(output, "")
        self.assertTrue(verify_password("a-strong-initial-password", self.admin_hash()))

    def test_unchanged_legacy_admin_password_is_rotated_on_startup(self):
        with self.sessions() as db:
            admin = User(email="admin", full_name="Super Admin", role="super_admin", hashed_password=hash_password("admin"))
            db.add(admin)
            db.flush()
            db.add(UserAccess(user_id=admin.id, password_change_required=True))
            db.commit()
        self.bootstrap("a-strong-initial-password")
        self.assertTrue(verify_password("a-strong-initial-password", self.admin_hash()))
        self.assertEqual(self.login("admin", "admin").status_code, 401)

    def test_changed_admin_password_is_never_overwritten(self):
        self.bootstrap("a-strong-initial-password")
        with self.sessions() as db:
            admin = db.scalar(select(User).where(User.email == "admin"))
            admin.hashed_password = hash_password("admins-own-password")
            db.get(UserAccess, admin.id).password_change_required = False
            db.commit()
        self.bootstrap("another-strong-password")
        self.assertTrue(verify_password("admins-own-password", self.admin_hash()))

    def test_repeated_failures_lock_account_even_for_correct_password(self):
        for _ in range(MAX_FAILURES):
            self.assertEqual(self.login("owner@example.com", "wrong-password").status_code, 401)
        locked = self.login("owner@example.com", "owner-password")
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Retry-After", locked.headers)
        oauth = self.client.post("/oauth/token", data={"username": "OWNER@example.com", "password": "owner-password"})
        self.assertEqual(oauth.status_code, 429)

    def test_lockout_expires_and_success_clears_failures(self):
        for _ in range(MAX_FAILURES):
            self.login("owner@example.com", "wrong-password")
        with self.sessions() as db:
            throttle = db.get(LoginThrottle, "owner@example.com")
            past = datetime.now(timezone.utc) - timedelta(minutes=16)
            throttle.locked_until = past
            throttle.window_started_at = past
            db.commit()
        self.assertEqual(self.login("owner@example.com", "owner-password").status_code, 200)
        with self.sessions() as db:
            self.assertIsNone(db.get(LoginThrottle, "owner@example.com"))

    def test_unknown_emails_are_throttled_like_real_ones(self):
        for _ in range(MAX_FAILURES):
            self.assertEqual(self.login("nobody@example.com", "wrong-password").status_code, 401)
        self.assertEqual(self.login("nobody@example.com", "wrong-password").status_code, 429)

    def test_landlord_cannot_claim_tenant_account_by_user_id(self):
        token = self.login("owner@example.com", "owner-password").json()["access_token"]
        response = self.client.post(
            "/tenants", headers={"Authorization": f"Bearer {token}"},
            json={"full_name": "Claimed", "user_id": self.tenant_user_id},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-tenant@example.com", response.text)
        with self.sessions() as db:
            profile = db.scalar(select(TenantProfile).where(TenantProfile.user_id == self.tenant_user_id))
            self.assertIsNone(profile.landlord_id)


if __name__ == "__main__":
    unittest.main()
