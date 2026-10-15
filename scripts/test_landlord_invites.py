"""Landlord invitation link tests against an isolated database."""

import os
import unittest
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
from app.invites import hash_code
from app.main import app
from app.models import LandlordInvite, LandlordProfile, User, UserAccess
from app.security import hash_password


class LandlordInviteTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(get_settings(), "jwt_secret_key", "test-only-invite-secret-0123456789abcdef").start()
        patch.object(get_settings(), "super_admin_initial_password", "initial-admin-password").start()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        with self.sessions() as db:
            bootstrap_super_admin(db)
            admin = db.scalar(select(User).where(User.email == "admin"))
            db.get(UserAccess, admin.id).password_change_required = False
            owner = User(email="owner@example.com", full_name="Owner", role="landlord", hashed_password=hash_password("owner-password"))
            db.add(owner)
            db.flush()
            db.add(LandlordProfile(user_id=owner.id))
            db.commit()

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.admin = self.headers(self.login("admin", "initial-admin-password").json()["access_token"])

    def login(self, email, password):
        return self.client.post("/auth/login", json={"email": email, "password": password})

    @staticmethod
    def headers(token):
        return {"Authorization": f"Bearer {token}"}

    def invite(self, **body):
        response = self.client.post("/admin/invites", headers=self.admin, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def register(self, code, email="new@example.com", role="landlord"):
        return self.client.post("/auth/register", json={
            "email": email, "full_name": "New", "password": "new-password", "role": role, "invite_code": code,
        })

    def test_invited_landlord_is_active_immediately_and_code_is_single_use(self):
        created = self.invite()
        self.assertEqual(created["status"], "active")
        response = self.register(created["code"])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["is_active"])
        self.assertEqual(self.login("new@example.com", "new-password").status_code, 200)
        self.assertEqual(self.register(created["code"], email="second@example.com").status_code, 400)
        listed = self.client.get("/admin/invites", headers=self.admin).json()
        self.assertEqual(listed[0]["status"], "used")
        self.assertEqual(listed[0]["used_by_email"], "new@example.com")
        self.assertNotIn("code", listed[0])

    def test_only_code_hash_is_stored(self):
        created = self.invite()
        with self.sessions() as db:
            stored = db.get(LandlordInvite, created["id"])
            self.assertEqual(stored.code_hash, hash_code(created["code"]))
            self.assertNotEqual(stored.code_hash, created["code"])

    def test_email_bound_invite_rejects_other_emails(self):
        created = self.invite(email="Invited@Example.com")
        self.assertEqual(self.register(created["code"], email="someone@example.com").status_code, 400)
        self.assertEqual(self.register(created["code"], email="invited@example.com").status_code, 201)

    def test_expired_revoked_and_unknown_codes_are_rejected(self):
        expired = self.invite()
        with self.sessions() as db:
            db.get(LandlordInvite, expired["id"]).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        revoked = self.invite()
        self.assertEqual(self.client.post(f"/admin/invites/{revoked['id']}/revoke", headers=self.admin).status_code, 204)
        for code in (expired["code"], revoked["code"], "not-a-real-code"):
            with self.subTest(code=code):
                self.assertEqual(self.register(code).status_code, 400)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(User).where(User.email == "new@example.com")))
        statuses = {item["id"]: item["status"] for item in self.client.get("/admin/invites", headers=self.admin).json()}
        self.assertEqual(statuses, {expired["id"]: "expired", revoked["id"]: "revoked"})

    def test_failed_registration_does_not_consume_invite(self):
        created = self.invite()
        self.assertEqual(self.register(created["code"], email="owner@example.com").status_code, 409)
        self.assertEqual(self.register(created["code"]).status_code, 201)

    def test_used_invite_cannot_be_revoked(self):
        created = self.invite()
        self.register(created["code"])
        self.assertEqual(self.client.post(f"/admin/invites/{created['id']}/revoke", headers=self.admin).status_code, 409)

    def test_invites_cannot_create_tenants(self):
        self.assertEqual(self.register(self.invite()["code"], role="tenant").status_code, 400)

    def test_only_super_admin_manages_invites(self):
        landlord = self.headers(self.login("owner@example.com", "owner-password").json()["access_token"])
        self.assertEqual(self.client.post("/admin/invites", headers=landlord, json={}).status_code, 403)
        self.assertEqual(self.client.get("/admin/invites", headers=landlord).status_code, 403)
        self.assertEqual(self.client.get("/admin/invites").status_code, 401)

    def test_expiry_is_bounded(self):
        for days in (0, 31):
            self.assertEqual(self.client.post("/admin/invites", headers=self.admin, json={"expires_in_days": days}).status_code, 422)

    def test_landlord_registration_without_invite_is_rejected(self):
        for code in (None, ""):
            response = self.client.post("/auth/register", json={
                "email": "plain@example.com", "full_name": "Plain", "password": "new-password", "role": "landlord",
                "invite_code": code,
            })
            self.assertEqual(response.status_code, 403)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(User).where(User.email == "plain@example.com")))


if __name__ == "__main__":
    unittest.main()
