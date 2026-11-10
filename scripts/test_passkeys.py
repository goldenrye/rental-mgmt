"""Real ES256 WebAuthn proofs against an isolated database, without an external authenticator."""

import base64
import hashlib
import json
import os
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.account_access import ensure_access
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import PasskeyChallenge, PasskeyCredential, PasswordResetToken, User
from app.password_reset import hash_code
from app.security import hash_password

RP = "rent.example.com"
ORIGIN = "https://" + RP


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class PasskeyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        for name, value in {
            "jwt_secret_key": "passkey-test-secret-0123456789abcdef",
            "passkey_rp_id": RP, "passkey_origins": ORIGIN,
            "passkey_apple_app_ids": "ABCDEFGHIJ.com.example.rental",
        }.items():
            patch.object(get_settings(), name, value).start()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        with self.sessions() as db:
            user = User(email="owner@example.com", full_name="Owner", role="landlord", hashed_password=hash_password("test-password"))
            other = User(email="other@example.com", full_name="Other", role="landlord", hashed_password=hash_password("test-password"))
            db.add_all([user, other])
            db.flush()
            ensure_access(db, user)
            ensure_access(db, other)
            db.commit()
            self.user_id, self.other_id = user.id, other.id

        def database():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = database
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = self.login()
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)

    def login(self, email="owner@example.com"):
        response = self.client.post("/auth/login", json={"email": email, "password": "test-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def start_registration(self, headers=None, password="test-password"):
        return self.client.post("/me/passkeys/register/options", headers=headers or self.headers,
                                json={"current_password": password, "name": "My iPhone"})

    def registration_proof(self, options, origin=ORIGIN, flags=0x45, rp=RP):
        key = self.private.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: key.x.to_bytes(32), -3: key.y.to_bytes(32)})
        auth = (hashlib.sha256(rp.encode()).digest() + bytes([flags]) + bytes(4) + bytes(16)
                + len(self.credential_id).to_bytes(2) + self.credential_id + cose)
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}).encode()
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key", "response": {
            "clientDataJSON": b64(client),
            "attestationObject": b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})),
        }}

    def register(self):
        response = self.start_registration()
        self.assertEqual(response.status_code, 200, response.text)
        ceremony = response.json()
        self.handle = ceremony["publicKey"]["user"]["id"]
        body = {"ceremony_id": ceremony["ceremony_id"], "credential": self.registration_proof(ceremony["publicKey"])}
        response = self.client.post("/me/passkeys/register/verify", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"], body

    def assertion(self, counter=1, origin=ORIGIN, flags=5, rp=RP, handle=None, signing_key=None, cross_origin=False):
        response = self.client.post("/auth/passkeys/options")
        self.assertEqual(response.status_code, 200, response.text)
        ceremony = response.json()
        client = json.dumps({"type": "webauthn.get", "challenge": ceremony["publicKey"]["challenge"],
                             "origin": origin, "crossOrigin": cross_origin}).encode()
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + counter.to_bytes(4)
        signature = (signing_key or self.private).sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"ceremony_id": ceremony["ceremony_id"], "credential": {
            "id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth),
                         "signature": b64(signature), "userHandle": handle or self.handle},
        }}

    def verify(self, body):
        return self.client.post("/auth/passkeys/verify", json=body)

    def test_register_logout_and_sign_in_without_password(self):
        key_id, _ = self.register()
        listed = self.client.get("/me/passkeys", headers=self.headers).json()
        self.assertEqual([item["id"] for item in listed], [key_id])
        self.assertNotIn("public_key", listed[0])
        self.assertEqual(self.client.post("/auth/logout", headers=self.headers).status_code, 204)
        response = self.verify(self.assertion())
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        me = self.client.get("/me", headers={"Authorization": "Bearer " + token})
        self.assertEqual(me.json()["id"], self.user_id)
        with self.sessions() as db:
            key = db.get(PasskeyCredential, key_id)
            self.assertEqual(key.sign_count, 1)
            self.assertIsNotNone(key.last_used_at)

    def test_registration_requires_password_session_and_user_verification(self):
        self.assertEqual(self.start_registration(password="wrong").status_code, 400)
        self.assertEqual(self.client.post("/me/passkeys/register/options", json={
            "current_password": "test-password", "name": "x",
        }).status_code, 401)
        start = self.start_registration().json()
        self.assertEqual(start["publicKey"]["authenticatorSelection"]["residentKey"], "required")
        body = {"ceremony_id": start["ceremony_id"], "credential": self.registration_proof(start["publicKey"], flags=0x41)}
        self.assertEqual(self.client.post("/me/passkeys/register/verify", headers=self.headers, json=body).status_code, 400)
        body["credential"] = self.registration_proof(start["publicKey"])
        self.assertEqual(self.client.post("/me/passkeys/register/verify", headers=self.headers, json=body).status_code, 400)

    def test_registration_is_bound_to_account_and_session(self):
        start = self.start_registration().json()
        body = {"ceremony_id": start["ceremony_id"], "credential": self.registration_proof(start["publicKey"])}
        for headers in [self.login("other@example.com"), self.login()]:
            self.assertEqual(self.client.post("/me/passkeys/register/verify", headers=headers, json=body).status_code, 400)
        self.assertEqual(self.client.post("/me/passkeys/register/verify", headers=self.headers, json=body).status_code, 201)

    def test_proofs_cannot_be_replayed_and_expired_challenges_fail(self):
        _, registration = self.register()
        self.assertEqual(self.client.post("/me/passkeys/register/verify", headers=self.headers, json=registration).status_code, 400)
        body = self.assertion()
        self.assertEqual(self.verify(body).status_code, 200)
        self.assertEqual(self.verify(body).status_code, 400)
        expired = self.assertion(counter=2)
        with self.sessions() as db:
            db.execute(update(PasskeyChallenge).where(PasskeyChallenge.id == expired["ceremony_id"])
                       .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            db.commit()
        self.assertEqual(self.verify(expired).status_code, 400)

    def test_invalid_signature_origin_rp_handle_presence_and_verification_fail(self):
        self.register()
        cases = [
            {"origin": "https://phishing.example"}, {"rp": "wrong.example"}, {"flags": 1}, {"flags": 4},
            {"handle": b64(b"another-account")}, {"signing_key": ec.generate_private_key(ec.SECP256R1())},
            {"cross_origin": True},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.verify(self.assertion(**case)).status_code, 400)
        body = self.assertion()
        body["credential"]["response"]["clientDataJSON"] = b64(json.dumps({
            "type": "webauthn.get", "challenge": "wrong-challenge", "origin": ORIGIN,
        }).encode())
        self.assertEqual(self.verify(body).status_code, 400)

    def test_synced_zero_counters_work_but_nonzero_counters_cannot_regress(self):
        self.register()
        self.assertEqual(self.verify(self.assertion(counter=0)).status_code, 200)
        self.assertEqual(self.verify(self.assertion(counter=0)).status_code, 200)
        self.assertEqual(self.verify(self.assertion(counter=2)).status_code, 200)
        self.assertEqual(self.verify(self.assertion(counter=1)).status_code, 400)

    def test_removal_is_account_scoped_and_revokes_future_sign_ins(self):
        key_id, _ = self.register()
        other = self.login("other@example.com")
        self.assertEqual(self.client.get("/me/passkeys", headers=other).json(), [])
        path = "/me/passkeys/" + key_id
        self.assertEqual(self.client.request("DELETE", path, headers=other, json={"current_password": "test-password"}).status_code, 404)
        self.assertEqual(self.client.request("DELETE", path, headers=self.headers, json={"current_password": "wrong"}).status_code, 400)
        body = self.assertion()
        self.assertEqual(self.client.request("DELETE", path, headers=self.headers, json={"current_password": "test-password"}).status_code, 204)
        self.assertEqual(self.verify(body).status_code, 400)

    def test_deactivation_and_forced_password_change_cannot_be_bypassed(self):
        self.register()
        with self.sessions() as db:
            ensure_access(db, db.get(User, self.user_id)).is_active = False
            db.commit()
        self.assertEqual(self.verify(self.assertion()).status_code, 403)
        with self.sessions() as db:
            state = ensure_access(db, db.get(User, self.user_id))
            state.is_active = True
            state.password_change_required = True
            db.commit()
        self.assertEqual(self.verify(self.assertion()).status_code, 403)

    def test_ceremony_purpose_and_duplicate_credentials(self):
        _, body = self.register()
        self.assertEqual(self.verify(body).status_code, 400)
        start = self.start_registration().json()
        self.assertEqual(start["publicKey"]["excludeCredentials"][0]["id"], b64(self.credential_id))
        body = {"ceremony_id": start["ceremony_id"], "credential": self.registration_proof(start["publicKey"])}
        self.assertEqual(self.client.post("/me/passkeys/register/verify", headers=self.headers, json=body).status_code, 409)

    def test_configuration_and_apple_association(self):
        self.assertEqual(self.client.get("/.well-known/apple-app-site-association").json(),
                         {"webcredentials": {"apps": ["ABCDEFGHIJ.com.example.rental"]}})
        for origin in ["http://" + RP, "https://evil.example", ORIGIN + "/path", "https://*.example.com"]:
            with patch.object(get_settings(), "passkey_origins", origin):
                self.assertFalse(self.client.get("/auth/passkeys/status").json()["available"])
                self.assertEqual(self.client.post("/auth/passkeys/options").status_code, 503)
        with patch.object(get_settings(), "passkey_rp_id", ""):
            self.assertFalse(self.client.get("/auth/passkeys/status").json()["available"])

    def test_rate_limit_options_and_payload_size(self):
        for _ in range(30):
            self.assertEqual(self.client.post("/auth/passkeys/options").status_code, 200)
        self.assertEqual(self.client.post("/auth/passkeys/options").status_code, 429)
        self.assertEqual(self.verify({"ceremony_id": "x" * 43, "credential": {"id": "a" * 40000}}).status_code, 422)

    def test_email_password_recovery_removes_passkeys_and_pending_registration(self):
        self.register()
        pending = self.start_registration().json()
        now = datetime.now(timezone.utc)
        code = "reset-token-" + secrets.token_urlsafe(32)
        with self.sessions() as db:
            db.add(PasswordResetToken(user_id=self.user_id, code_hash=hash_code(code),
                                      created_at=now, expires_at=now + timedelta(hours=1)))
            db.commit()
        response = self.client.post("/auth/password-reset/confirm", json={"token": code, "new_password": "new-password"})
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.verify(self.assertion()).status_code, 400)
        with self.sessions() as db:
            self.assertIsNone(db.get(PasskeyChallenge, pending["ceremony_id"]))
        response = self.client.post("/auth/login", json={"email": "owner@example.com", "password": "new-password"})
        self.assertEqual(response.status_code, 200)

    def test_admin_password_reset_removes_passkeys(self):
        self.register()
        with self.sessions() as db:
            admin = User(email="admin@example.com", full_name="Admin", role="super_admin", hashed_password=hash_password("test-password"))
            db.add(admin)
            db.commit()
        admin_headers = self.login("admin@example.com")
        response = self.client.post(f"/admin/landlords/{self.user_id}/password",
                                    headers=admin_headers, json={"new_password": "replacement-password"})
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.verify(self.assertion()).status_code, 400)


if __name__ == "__main__":
    unittest.main()
