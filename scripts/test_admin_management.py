"""Admin management and session revocation tests against an isolated database."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import mcp_server
from app.account_access import bootstrap_super_admin
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import AdminAuditEvent, LandlordProfile, LoginSession, RentalProperty, User, UserAccess
from app.security import create_access_token, decode_access_token, hash_password, verify_password


class AdminManagementTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(get_settings(), "jwt_secret_key", "test-only-admin-secret-0123456789abcdef").start()
        patch.object(get_settings(), "super_admin_initial_password", "initial-admin-password").start()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        patch.object(mcp_server, "SessionLocal", self.sessions).start()
        with self.sessions() as db:
            bootstrap_super_admin(db)
            owner = User(email="owner@example.com", full_name="Owner", role="landlord", hashed_password=hash_password("owner-password"))
            other = User(email="other@example.com", full_name="Other", role="landlord", hashed_password=owner.hashed_password)
            tenant = User(email="tenant@example.com", full_name="Tenant", role="tenant", hashed_password=owner.hashed_password)
            db.add_all([owner, other, tenant])
            db.flush()
            landlord = LandlordProfile(user_id=owner.id)
            db.add_all([landlord, LandlordProfile(user_id=other.id)])
            db.flush()
            db.add(RentalProperty(landlord_id=landlord.id, name="Preserved house", address="Test"))
            db.commit()
            self.owner_id, self.other_id, self.tenant_id = owner.id, other.id, tenant.id
            self.admin_id = db.scalar(select(User.id).where(User.email == "admin"))

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.initial_token = self.login("admin", "initial-admin-password")

    def login(self, name, password):
        response = self.client.post("/auth/login", json={"email": name, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    @staticmethod
    def headers(token):
        return {"Authorization": f"Bearer {token}"}

    def unlock_admin(self):
        response = self.client.post("/me/password", headers=self.headers(self.initial_token), json={
            "current_password": "initial-admin-password", "new_password": "secure-admin-password",
        })
        self.assertEqual(response.status_code, 204, response.text)
        self.admin_token = self.login("admin", "secure-admin-password")
        return self.headers(self.admin_token)

    def test_default_admin_requires_change_and_is_not_recreated(self):
        me = self.client.get("/me", headers=self.headers(self.initial_token)).json()
        self.assertEqual(me["role"], "super_admin")
        self.assertTrue(me["password_change_required"])
        self.assertEqual(self.client.get("/admin/landlords", headers=self.headers(self.initial_token)).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/landlords/{self.owner_id}/password",
            headers=self.headers(self.initial_token), json={"new_password": "blocked-password"}).status_code, 403)
        self.unlock_admin()
        with self.sessions() as db:
            bootstrap_super_admin(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(User).where(User.email == "admin")), 1)
            self.assertTrue(verify_password("secure-admin-password", db.get(User, self.admin_id).hashed_password))
        self.assertEqual(self.client.get("/me", headers=self.headers(self.initial_token)).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={"email": "admin", "password": "initial-admin-password"}).status_code, 401)

    def test_admin_only_has_landlord_management_access(self):
        headers = self.unlock_admin()
        for path in ["/properties", "/units", "/tenants", "/leases", "/bills", "/payments", "/property-bills", "/tenant/bills"]:
            self.assertEqual(self.client.get(path, headers=headers).status_code, 403, path)
        with self.assertRaises(ValueError):
            mcp_server.validate_mcp_bearer_token(self.admin_token)
        self.assertEqual(self.client.post("/oauth/token", data={
            "username": "admin", "password": "secure-admin-password",
        }).status_code, 403)
        accounts = self.client.get("/admin/landlords", headers=headers).json()
        self.assertEqual({item["user_id"] for item in accounts}, {self.owner_id, self.other_id})
        self.assertNotIn("hashed_password", accounts[0])

    def test_nonadmins_and_registration_cannot_gain_admin_access(self):
        for name in ["owner@example.com", "tenant@example.com"]:
            headers = self.headers(self.login(name, "owner-password"))
            self.assertEqual(self.client.get("/admin/landlords", headers=headers).status_code, 403)
            self.assertEqual(self.client.patch(f"/admin/landlords/{self.owner_id}/status", headers=headers, json={"is_active": False}).status_code, 403)
            self.assertEqual(self.client.post(f"/admin/landlords/{self.owner_id}/password", headers=headers, json={"new_password": "hacked-password"}).status_code, 403)
        response = self.client.post("/auth/register", json={
            "email": "attacker@example.com", "full_name": "Attacker", "password": "test-password", "role": "super_admin",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/admin/landlords").status_code, 401)

    def test_deactivation_revokes_api_mcp_and_legacy_tokens(self):
        headers = self.unlock_admin()
        web = self.login("owner@example.com", "owner-password")
        oauth = self.client.post("/oauth/token", data={"username": "owner@example.com", "password": "owner-password"}).json()["access_token"]
        legacy = create_access_token(str(self.owner_id))
        other = self.login("other@example.com", "owner-password")
        mcp_server.validate_mcp_bearer_token(oauth)
        path = f"/admin/landlords/{self.owner_id}/status"
        self.assertEqual(self.client.patch(path, headers=headers, json={"is_active": False}).status_code, 204)
        for token in [web, oauth, legacy]:
            self.assertEqual(self.client.get("/me", headers=self.headers(token)).status_code, 401)
            with self.assertRaises(ValueError):
                mcp_server.validate_mcp_bearer_token(token)
        self.assertEqual(self.client.get("/me", headers=self.headers(other)).status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"email": "owner@example.com", "password": "owner-password"}).status_code, 403)
        self.assertEqual(self.client.post("/oauth/token", data={"username": "owner@example.com", "password": "owner-password"}).status_code, 403)
        self.assertEqual(self.client.patch(path, headers=headers, json={"is_active": True}).status_code, 204)
        self.assertEqual(self.client.get("/me", headers=self.headers(web)).status_code, 401)
        self.login("owner@example.com", "owner-password")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RentalProperty)), 1)
            self.assertEqual(db.scalars(select(AdminAuditEvent.action).order_by(AdminAuditEvent.id)).all(), ["deactivate", "activate"])

    def test_password_reset_revokes_all_sessions_and_preserves_other_accounts(self):
        headers = self.unlock_admin()
        old = self.login("owner@example.com", "owner-password")
        other = self.login("other@example.com", "owner-password")
        response = self.client.post(f"/admin/landlords/{self.owner_id}/password", headers=headers, json={"new_password": "reset-password"})
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.client.get("/me", headers=self.headers(old)).status_code, 401)
        with self.assertRaises(ValueError):
            mcp_server.validate_mcp_bearer_token(old)
        self.assertEqual(self.client.get("/me", headers=self.headers(other)).status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"email": "owner@example.com", "password": "owner-password"}).status_code, 401)
        self.login("owner@example.com", "reset-password")
        with self.sessions() as db:
            self.assertTrue(verify_password("reset-password", db.get(User, self.owner_id).hashed_password))
            self.assertEqual(db.scalar(select(AdminAuditEvent.action)), "reset_password")

    def test_login_history_multiple_sessions_logout_and_expiry(self):
        headers = self.unlock_admin()
        first = self.login("owner@example.com", "owner-password")
        second = self.login("owner@example.com", "owner-password")
        def account():
            return next(item for item in self.client.get("/admin/landlords", headers=headers).json() if item["user_id"] == self.owner_id)
        row = account()
        self.assertEqual(row["active_sessions"], 2)
        self.assertEqual(row["login_status"], "signed_in")
        self.assertTrue(row["last_login_at"])
        self.assertTrue(row["last_seen_at"])
        self.assertEqual(self.client.post("/auth/logout", headers=self.headers(first)).status_code, 204)
        self.assertEqual(self.client.get("/me", headers=self.headers(first)).status_code, 401)
        with self.assertRaises(ValueError):
            mcp_server.validate_mcp_bearer_token(first)
        self.assertEqual(account()["active_sessions"], 1)
        with self.sessions() as db:
            db.get(LoginSession, decode_access_token(second)["jti"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(account()["login_status"], "signed_out")
        self.assertEqual(self.client.get("/me", headers=self.headers(second)).status_code, 401)

    def test_management_rejects_tenant_admin_and_unknown_targets(self):
        headers = self.unlock_admin()
        for target in [self.tenant_id, self.admin_id, 9999]:
            self.assertEqual(self.client.patch(f"/admin/landlords/{target}/status", headers=headers, json={"is_active": False}).status_code, 404)
            self.assertEqual(self.client.post(f"/admin/landlords/{target}/password", headers=headers, json={"new_password": "reset-password"}).status_code, 404)
        self.assertEqual(self.client.post(f"/admin/landlords/{self.owner_id}/password", headers=headers, json={"new_password": "short"}).status_code, 422)

    def test_existing_accounts_default_active_and_no_history(self):
        headers = self.unlock_admin()
        accounts = self.client.get("/admin/landlords", headers=headers).json()
        self.assertTrue(all(item["is_active"] for item in accounts))
        self.assertTrue(all(item["login_status"] == "never_logged_in" for item in accounts))
        self.login("owner@example.com", "owner-password")

    def test_landlord_email_normalization_is_preserved(self):
        self.login("owner@EXAMPLE.COM", "owner-password")

    def test_password_reset_does_not_reactivate_account(self):
        headers = self.unlock_admin()
        self.client.patch(f"/admin/landlords/{self.owner_id}/status", headers=headers, json={"is_active": False})
        self.client.post(f"/admin/landlords/{self.owner_id}/password", headers=headers, json={"new_password": "reset-password"})
        self.assertEqual(self.client.post("/auth/login", json={"email": "owner@example.com", "password": "reset-password"}).status_code, 403)

    def test_user_password_change_revokes_previous_sessions(self):
        token = self.login("owner@example.com", "owner-password")
        self.assertEqual(self.client.patch("/me", headers=self.headers(token), json={
            "full_name": "Updated Owner", "current_password": "owner-password", "new_password": "new-owner-password",
        }).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=self.headers(token)).status_code, 401)
        self.login("owner@example.com", "new-owner-password")

    def test_activity_updates_and_missing_sessions_are_rejected(self):
        token = self.login("owner@example.com", "owner-password")
        session_id = decode_access_token(token)["jti"]
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        with self.sessions() as db:
            db.get(LoginSession, session_id).last_seen_at = old
            db.commit()
        mcp_server.validate_mcp_bearer_token(token)
        with self.sessions() as db:
            session = db.get(LoginSession, session_id)
            self.assertGreater(session.last_seen_at.replace(tzinfo=timezone.utc), old)
            db.delete(session)
            db.commit()
        self.assertEqual(self.client.get("/me", headers=self.headers(token)).status_code, 401)
        with self.assertRaises(ValueError):
            mcp_server.validate_mcp_bearer_token(token)

    def test_failed_login_does_not_record_success_and_password_limits(self):
        self.assertEqual(self.client.post("/auth/login", json={
            "email": "owner@example.com", "password": "wrong",
        }).status_code, 401)
        with self.sessions() as db:
            self.assertIsNone(db.get(UserAccess, self.owner_id))
        headers = self.unlock_admin()
        too_long_utf8 = "\u00e9" * 40
        self.assertEqual(self.client.post(f"/admin/landlords/{self.owner_id}/password",
            headers=headers, json={"new_password": too_long_utf8}).status_code, 422)
        self.assertEqual(self.client.post("/me/password", headers=headers, json={
            "current_password": "secure-admin-password", "new_password": too_long_utf8,
        }).status_code, 422)


if __name__ == "__main__":
    unittest.main()
