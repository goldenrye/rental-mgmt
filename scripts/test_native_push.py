"""iPhone app (APNs) push tests use an isolated database and never contact Apple."""

import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from pydantic import SecretStr
from sqlalchemy import select

import test_web_push as web_tests
from app import apns, web_push
from app.config import get_settings
from app.models import NativePushDevice, WebPushNotice

TODAY = web_tests.TODAY
TOKEN = "ab" * 32


class NativePushTests(unittest.TestCase):
    # Reuse the web push fixture (two landlords with leases and bills) without re-running its tests.
    headers = web_tests.WebPushTests.headers
    reconcile = web_tests.WebPushTests.reconcile

    def setUp(self):
        web_tests.WebPushTests.setUp(self)
        self.apns_key = ec.generate_private_key(ec.SECP256R1())
        pem = self.apns_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
        for key, value in {
            "apns_team_id": "TEAM123456", "apns_key_id": "KEY1234567", "apns_topic": "com.example.rental",
            # Single-line env style, as an .env file would hold it.
            "apns_auth_key": SecretStr(pem.replace("\n", "\\n")),
        }.items():
            patcher = patch.object(get_settings(), key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pushed = []
        self.responses = []

        def send(token, environment, message):
            self.pushed.append((token, environment, message))
            if self.responses:
                result = self.responses.pop(0)
                if result:
                    raise result
        self.send_patch = patch.object(apns, "send", side_effect=send)
        self.send = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)

    def register(self, owner=0, token=TOKEN):
        return self.client.post("/me/native-push/devices", headers=self.headers(owner), json={"token": token})

    def test_status_and_validation(self):
        status = self.client.get("/me/web-push", headers=self.headers()).json()
        self.assertTrue(status["native_available"])
        for token in ("", "xyz" * 22, "ab" * 10, "a" * 65, TOKEN + "/../x"):
            self.assertEqual(self.register(token=token).status_code, 422, token)
        self.assertEqual(self.client.post("/me/native-push/devices", json={"token": TOKEN}).status_code, 401)
        response = self.register(token=TOKEN.upper())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["devices"], 1)
        with patch.object(get_settings(), "apns_topic", ""):
            self.assertFalse(self.client.get("/me/web-push", headers=self.headers()).json()["native_available"])
            self.assertEqual(self.register(token="cd" * 32).status_code, 503)

    def test_token_is_sealed_and_moves_with_sign_in(self):
        self.register(0)
        with self.sessions() as db:
            row = db.scalar(select(NativePushDevice))
            self.assertNotIn(TOKEN, row.token)
            self.assertEqual(web_push.unseal_device_token(row), TOKEN)
        self.assertEqual(self.register(1).json()["devices"], 1)
        self.assertEqual(self.client.get("/me/web-push", headers=self.headers(0)).json()["devices"], 0)
        self.client.request("DELETE", "/me/native-push/devices", headers=self.headers(0), json={"token": TOKEN})
        self.assertEqual(self.client.get("/me/web-push", headers=self.headers(1)).json()["devices"], 1)
        response = self.client.request("DELETE", "/me/native-push/devices", headers=self.headers(1), json={"token": TOKEN})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/me/web-push", headers=self.headers(1)).json()["devices"], 0)

    def test_reminders_reach_phone_and_browser_once(self):
        self.register(0)
        self.subscribe(0)
        self.reconcile()
        self.assertEqual([m["tag"] for _, _, m in self.pushed], ["lease-expiry", "bill-due"])
        self.assertEqual([m["tag"] for _, m in self.sent], ["lease-expiry", "bill-due"])
        self.assertNotIn("Private", json.dumps(self.pushed))
        self.pushed.clear()
        self.reconcile(TODAY + timedelta(days=1))
        self.assertEqual(self.pushed, [])

    def test_phone_only_landlords_get_reminders_without_web_push(self):
        self.register(1)
        with patch.object(get_settings(), "web_push_contact", ""):
            self.reconcile()
        self.assertEqual(len(self.pushed), 2)
        self.deliver.assert_not_called()
        with self.sessions() as db:
            self.assertEqual({n.landlord_id for n in db.scalars(select(WebPushNotice))}, {self.owners[1][0]})

    def test_debug_build_tokens_fall_back_to_sandbox_and_remember_it(self):
        self.register(0)
        self.responses = [apns.ApnsError(400, "BadDeviceToken"), None]
        response = self.client.post("/me/web-push/test", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([env for _, env, _ in self.pushed], ["production", "sandbox"])
        self.pushed.clear()
        self.client.post("/me/web-push/test", headers=self.headers())
        self.assertEqual([env for _, env, _ in self.pushed], ["sandbox"])

    def test_failures_retry_and_dead_tokens_are_removed(self):
        self.register(0)
        self.send.side_effect = httpx.ConnectError("offline")
        self.reconcile()
        with self.sessions() as db:
            self.assertEqual(list(db.scalars(select(WebPushNotice))), [])
            self.assertIsNotNone(db.scalar(select(NativePushDevice)))
        self.send.side_effect = apns.ApnsError(500, "InternalServerError")
        self.reconcile()
        with self.sessions() as db:
            self.assertIsNotNone(db.scalar(select(NativePushDevice)))
        self.send.side_effect = apns.ApnsError(410, "Unregistered")
        self.reconcile()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(NativePushDevice)))
            self.assertEqual(list(db.scalars(select(WebPushNotice))), [])

    def test_real_request_is_signed_for_apple(self):
        self.send_patch.stop()
        self.register(0)
        posted = []

        real = apns.client()
        self.assertFalse(real.follow_redirects)
        self.assertTrue(real._transport._pool._http2)
        fake = type("Client", (), {"post": lambda _, url, **kwargs: posted.append((url, kwargs)) or httpx.Response(200)})()
        with patch.object(apns, "_provider_token", (None, 0.0, None)), patch.object(apns, "client", lambda: fake):
            response = self.client.post("/me/web-push/test", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        url, kwargs = posted[0]
        self.assertEqual(url, f"https://api.push.apple.com/3/device/{TOKEN}")
        headers = kwargs["headers"]
        self.assertEqual((headers["apns-topic"], headers["apns-push-type"], headers["apns-collapse-id"]),
                         ("com.example.rental", "alert", "test"))
        token = headers["authorization"].removeprefix("bearer ")
        self.assertEqual(jwt.get_unverified_header(token)["kid"], "KEY1234567")
        claims = jwt.decode(token, self.apns_key.public_key(), algorithms=["ES256"])
        self.assertEqual(claims["iss"], "TEAM123456")
        self.assertEqual(kwargs["json"]["aps"]["alert"]["body"], "Test notification: reminders will appear like this.")

    subscribe = web_tests.WebPushTests.subscribe


if __name__ == "__main__":
    unittest.main()
