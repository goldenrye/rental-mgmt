"""API-key lifecycle and real MCP transport isolation, using an in-memory DB."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from scripts import test_mcp_isolation as fixture
from app import mcp_server as server
from app.models import McpApiKey, User, UserAccess
from app.security import create_access_token


class McpApiKeyTests(unittest.TestCase):
    login = fixture.McpIsolationTests.login
    rpc_body = staticmethod(fixture.McpIsolationTests.rpc_body)
    initialize = fixture.McpIsolationTests.initialize
    call = fixture.McpIsolationTests.call
    values = fixture.McpIsolationTests.values

    def setUp(self):
        fixture.McpIsolationTests.setUp(self)
        self.jwt_tokens = self.tokens[:]
        self.created = [self.create_key(index) for index in (0, 1)]
        self.tokens = [key["api_key"] for key in self.created]
        self.session_ids = [self.initialize(token) for token in self.tokens]

    def create_key(self, owner=0, **fields):
        response = self.api.post("/me/mcp-api-keys", headers=self.headers(owner), json={
            "name": "Agent", "current_password": "test-password", **fields,
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def headers(self, owner=0):
        return {"Authorization": f"Bearer {self.jwt_tokens[owner]}"}

    def request_tools(self, owner=0, headers=None):
        return self.client.post("/mcp", headers=headers or {
            "Authorization": f"Bearer {self.tokens[owner]}", "mcp-session-id": self.session_ids[owner],
        }, json={"jsonrpc": "2.0", "id": 90, "method": "tools/list"})

    test_all_list_tools_isolate_interleaved_accounts = fixture.McpIsolationTests.test_all_list_tools_isolate_interleaved_accounts
    test_filters_cannot_select_another_landlord = fixture.McpIsolationTests.test_filters_cannot_select_another_landlord
    test_detail_tools_and_foreign_filters_are_scoped = fixture.McpIsolationTests.test_detail_tools_and_foreign_filters_are_scoped
    test_other_account_cannot_reuse_mcp_session = fixture.McpIsolationTests.test_other_account_cannot_reuse_mcp_session
    test_sse_messages_use_request_identity_and_bind_session_owner = fixture.McpIsolationTests.test_sse_messages_use_request_identity_and_bind_session_owner

    def test_keys_unique_hashed_and_listed_only_to_owner(self):
        key = self.create_key()
        self.assertNotEqual(key["api_key"], self.tokens[0])
        self.assertNotEqual(self.tokens[0], self.tokens[1])
        self.assertEqual(len(key["api_key"]), 48)
        with self.sessions() as db:
            stored = db.get(McpApiKey, key["id"])
            self.assertNotEqual(stored.key_hash, key["api_key"])
            self.assertEqual(len(stored.key_hash), 64)
            self.assertEqual(stored.user_id, self.owners[0]["user_id"])
            dump = "\n".join(db.connection().connection.driver_connection.iterdump())
            self.assertNotIn(key["api_key"], dump)
            self.assertNotIn(self.tokens[0], dump)
        response = self.api.get("/me/mcp-api-keys", headers=self.headers())
        self.assertEqual({item["id"] for item in response.json()}, {key["id"], self.created[0]["id"]})
        self.assertNotIn("api_key", response.json()[0])
        self.assertNotIn("key_hash", response.text)

    def test_management_requires_landlord_login_and_password(self):
        for payload in [
            {"name": "Agent", "current_password": "wrong"},
            {"name": " ", "current_password": "test-password"},
            {"name": "Agent", "current_password": "test-password", "expires_in_days": 0},
            {"name": "Agent", "current_password": "test-password", "expires_in_days": 366},
        ]:
            self.assertIn(self.api.post("/me/mcp-api-keys", headers=self.headers(), json=payload).status_code, [400, 422])
        for role in ("tenant", "super_admin"):
            with self.sessions() as db:
                user = User(email=f"{role}@example.com", full_name=role, role=role, hashed_password="unused")
                db.add(user)
                db.commit()
                token = create_access_token(str(user.id))
            for method, path, kwargs in [
                ("get", "/me/mcp-api-keys", {}),
                ("post", "/me/mcp-api-keys", {"json": {"name": "Agent", "current_password": "test-password"}}),
                ("delete", f"/me/mcp-api-keys/{self.created[0]['id']}", {}),
            ]:
                self.assertEqual(getattr(self.api, method)(path, headers={"Authorization": f"Bearer {token}"}, **kwargs).status_code, 403)
        self.assertEqual(self.api.get("/me/mcp-api-keys").status_code, 401)
        for path in ("/me", "/properties", "/me/mcp-api-keys", "/admin/landlords"):
            self.assertEqual(self.api.get(path, headers={"Authorization": f"Bearer {self.tokens[0]}"}).status_code, 401)
        self.assertEqual(self.api.delete(f"/me/mcp-api-keys/{self.created[0]['id']}", headers=self.headers(1)).status_code, 404)

    def test_revocation_invalidates_existing_sessions_and_direct_dispatch(self):
        self.assertEqual(self.request_tools().status_code, 200)
        path = f"/me/mcp-api-keys/{self.created[0]['id']}"
        for _ in range(2):
            self.assertEqual(self.api.delete(path, headers=self.headers()).status_code, 204)
        self.assertEqual(self.request_tools().status_code, 401)
        request = Request({"type": "http", "headers": [(b"x-api-key", self.tokens[0].encode())]})
        with patch.object(server.mcp, "get_context", return_value=SimpleNamespace(request_context=SimpleNamespace(request=request))):
            with self.assertRaises(ValueError):
                server.list_properties()
        self.assertEqual(self.request_tools(1).status_code, 200)

    def test_expiration_and_tampering_are_rejected(self):
        with self.sessions() as db:
            db.get(McpApiKey, self.created[0]["id"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.request_tools().status_code, 401)
        for secret in ["rmcp_bad", self.tokens[1] + "x", "rmcp_" + "a" * 43, "rmcp_" + "\u00e9" * 43]:
            with self.assertRaises(ValueError):
                server.validate_mcp_bearer_token(secret)
        response = self.api.get("/me/mcp-api-keys", headers=self.headers())
        self.assertEqual(response.json()[0]["status"], "expired")
        timed = self.create_key(expires_in_days=30)
        self.assertIsNotNone(timed["expires_at"])

    def test_keys_survive_login_logout_and_jwt_rotation(self):
        self.login(self.owners[0]["email"])
        self.assertEqual(self.api.post("/auth/logout", headers=self.headers()).status_code, 204)
        self.assertEqual(self.request_tools().status_code, 200)
        with patch.object(fixture.get_settings(), "jwt_secret_key", "another-test-secret-0123456789abcdef"):
            self.assertEqual(self.request_tools().status_code, 200)

    def test_password_change_revokes_keys(self):
        response = self.api.post("/me/password", headers=self.headers(), json={
            "current_password": "test-password", "new_password": "replacement-password",
        })
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.request_tools().status_code, 401)
        self.assertEqual(self.request_tools(1).status_code, 200)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(McpApiKey, self.created[0]["id"]).revoked_at)

    def test_admin_reset_and_deactivation_revoke_keys_permanently(self):
        with self.sessions() as db:
            admin = User(email="key-admin@example.com", full_name="Admin", role="super_admin", hashed_password="unused")
            db.add(admin)
            db.commit()
            headers = {"Authorization": f"Bearer {create_access_token(str(admin.id))}"}
        path = f"/admin/landlords/{self.owners[0]['user_id']}/status"
        self.assertEqual(self.api.patch(path, headers=headers, json={"is_active": False}).status_code, 204)
        self.assertEqual(self.request_tools().status_code, 401)
        self.assertEqual(self.api.patch(path, headers=headers, json={"is_active": True}).status_code, 204)
        self.assertEqual(self.request_tools().status_code, 401)
        path = f"/admin/landlords/{self.owners[1]['user_id']}/password"
        self.assertEqual(self.api.post(path, headers=headers, json={"new_password": "reset-password"}).status_code, 204)
        self.assertEqual(self.request_tools(1).status_code, 401)

    def test_account_status_role_missing_user_and_version_checked(self):
        for change in ("inactive", "role", "version", "missing"):
            with self.sessions() as db:
                user = db.get(User, self.owners[0]["user_id"])
                state = db.get(UserAccess, user.id)
                if change == "inactive":
                    state.is_active = False
                elif change == "role":
                    state.is_active = True
                    user.role = "tenant"
                elif change == "version":
                    user.role = "landlord"
                    state.token_version += 1
                else:
                    db.execute(User.__table__.delete().where(User.id == user.id))
                db.commit()
            self.assertEqual(self.request_tools().status_code, 401)

    def test_header_and_stdio_modes_never_use_ambient_key_for_http(self):
        self.assertEqual(self.request_tools(headers={
            "X-API-Key": self.tokens[0], "mcp-session-id": self.session_ids[0],
        }).status_code, 200)
        # Ambiguous credentials, query-string credentials, and JWTs in API-key headers fail.
        for headers in [
            {"Authorization": f"Bearer {self.tokens[0]}", "X-API-Key": self.tokens[1]},
            {"X-API-Key": self.jwt_tokens[0]},
            [("X-API-Key", self.tokens[0]), ("X-API-Key", self.tokens[0])],
        ]:
            self.assertEqual(self.request_tools(headers=headers).status_code, 401)
        self.assertEqual(self.client.get(f"/mcp?api_key={self.tokens[0]}").status_code, 401)
        with patch.object(server, "MCP_TRANSPORT", "stdio"), patch.dict(os.environ, {
            "RENTAL_MGMT_MCP_API_KEY": self.tokens[0], "RENTAL_MGMT_MCP_TOKEN": self.jwt_tokens[1],
        }):
            self.assertEqual(server.list_properties()[0]["landlord_id"], self.owners[0]["id"])
            request = Request({"type": "http", "headers": []})
            with patch.object(server.mcp, "get_context", return_value=SimpleNamespace(request_context=SimpleNamespace(request=request))):
                with self.assertRaises(ValueError):
                    server.list_properties()
        with self.sessions() as db:
            self.assertIsNotNone(db.get(McpApiKey, self.created[0]["id"]).last_used_at)


if __name__ == "__main__":
    unittest.main()
