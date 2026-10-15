"""OAuth and real MCP transport isolation tests using only an in-memory database."""

import asyncio
import json
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import jwt
import httpx
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import mcp_server as server
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import (
    LandlordProfile, Lease, LeaseTenant, Payment, PropertyBill,
    RentalBill, RentalProperty, RentalUnit, TenantProfile, User,
)
from app.security import create_access_token, decode_access_token, hash_password


TOOLS = [
    "database_status", "list_landlords", "list_properties", "list_tenants",
    "list_leases", "list_property_bills", "list_rental_bills", "list_payments",
    "get_tenant_payment_history", "get_property_overview",
]
LIST_TOOLS = TOOLS[2:8]


class McpIsolationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(get_settings(), "jwt_secret_key", "test-only-random-secret-0123456789abcdef").start()
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        patch.object(server, "SessionLocal", self.sessions).start()
        patch.object(server, "MCP_TRANSPORT", "streamable-http").start()
        patch.object(server, "MCP_AUTH_ENABLED", True).start()
        password_hash = hash_password("test-password")
        self.owners = []
        with self.sessions() as db:
            # Make user IDs differ from landlord IDs to catch incorrect identity mapping.
            db.add(User(email="tenant-login@example.com", full_name="Tenant login",
                        role="tenant", hashed_password=password_hash))
            db.flush()
            for index in range(2):
                owner = LandlordProfile(user=User(
                    email=f"owner{index}@example.com", full_name=f"Owner {index}",
                    role="landlord", hashed_password=password_hash,
                ))
                property_ = RentalProperty(landlord=owner, name=f"Property {index}", address="Private")
                unit = RentalUnit(property=property_, name=f"Unit {index}")
                tenant = TenantProfile(landlord=owner, full_name=f"Tenant {index}", notes="Private")
                db.add_all([owner, property_, unit, tenant])
                db.flush()
                lease = Lease(landlord_id=owner.id, property=property_, unit=unit,
                              start_date=date(2026, 1, 1), end_date=date(2027, 1, 1), monthly_rent=100)
                lease.tenants = [LeaseTenant(tenant=tenant, monthly_rent=100, deposit=50)]
                db.add(lease)
                db.flush()
                bill = RentalBill(landlord_id=owner.id, lease=lease, tenant=tenant,
                                  bill_year=2026, bill_month=10, due_date=date(2026, 10, 31),
                                  amount_due=100, amount_paid=10, balance=90, status="partial")
                db.add(bill)
                db.flush()
                db.add_all([
                    PropertyBill(landlord_id=owner.id, property=property_, title="Water",
                                 bill_type="water", recurrence="one_time",
                                 due_date=date(2026, 10, 31), amount_due=40),
                    Payment(landlord_id=owner.id, tenant_id=tenant.id, bill=bill,
                            amount=10, paid_at=datetime.now(timezone.utc)),
                ])
                self.owners.append(dict(
                    id=owner.id, user_id=owner.user_id, email=owner.user.email,
                    property_id=property_.id, tenant_id=tenant.id,
                ))
            db.commit()

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.api = TestClient(app)
        self.addCleanup(self.api.close)
        self.tokens = [self.login(owner["email"]) for owner in self.owners]
        # A fresh SDK instance lets each test exercise the real session manager lifespan.
        mcp = FastMCP("isolation-test", host="127.0.0.1")
        for name in TOOLS:
            mcp.add_tool(getattr(server, name))
        patch.object(server, "mcp", mcp).start()
        self.client = TestClient(
            server.secure_remote_mcp_app(server.EnsureMcpAcceptHeader(mcp.streamable_http_app())),
            base_url="http://127.0.0.1:8001",
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.request_id = 0
        self.session_ids = [self.initialize(token) for token in self.tokens]

    def login(self, email, password="test-password"):
        response = self.api.post("/oauth/token", data={
            "grant_type": "password", "username": email, "password": password,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    @staticmethod
    def rpc_body(response):
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))

    def initialize(self, token):
        response = self.client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertEqual(response.status_code, 200, response.text)
        session = response.headers["mcp-session-id"]
        response = self.client.post("/mcp", headers={
            "Authorization": f"Bearer {token}", "mcp-session-id": session,
        }, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(response.status_code, 202, response.text)
        return session

    def call(self, owner, name, arguments=None):
        self.request_id += 1
        response = self.client.post("/mcp", headers={
            "Authorization": f"Bearer {self.tokens[owner]}",
            "mcp-session-id": self.session_ids[owner],
        }, json={"jsonrpc": "2.0", "id": self.request_id, "method": "tools/call",
                 "params": {"name": name, "arguments": arguments or {}}})
        self.assertEqual(response.status_code, 200, response.text)
        return self.rpc_body(response)["result"]

    def values(self, result):
        self.assertFalse(result.get("isError"), result)
        return [json.loads(item["text"]) for item in result["content"] if item["type"] == "text"]

    def test_tokens_unique_per_login_and_bound_to_correct_user(self):
        for owner, token in zip(self.owners, self.tokens):
            new_token = self.login(owner["email"])
            self.assertNotEqual(token, new_token)
            claims = decode_access_token(token)
            self.assertEqual(claims["sub"], str(owner["user_id"]))
            self.assertIn("jti", claims)
        with patch("app.security.datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.assertNotEqual(create_access_token("1"), create_access_token("1"))

    def test_wrong_credentials_and_tenant_login_rejected(self):
        for email, password, expected in [
            (self.owners[0]["email"], "wrong", 401),
            ("missing@example.com", "test-password", 401),
            ("tenant-login@example.com", "test-password", 403),
        ]:
            response = self.api.post("/oauth/token", data={
                "grant_type": "password", "username": email, "password": password,
            })
            self.assertEqual(response.status_code, expected)

    def test_all_list_tools_isolate_interleaved_accounts(self):
        for index in (0, 1, 0, 1):
            owner = self.owners[index]
            for name in LIST_TOOLS:
                with self.subTest(owner=index, tool=name):
                    rows = self.values(self.call(index, name))
                    self.assertEqual(len(rows), 1, rows)
                    self.assertEqual(rows[0]["landlord_id"], owner["id"])
            profile = self.values(self.call(index, "list_landlords"))
            self.assertEqual([row["email"] for row in profile], [owner["email"]])
            status = self.values(self.call(index, "database_status"))[0]
            self.assertEqual(status, {"status": "ok", "landlord_id": owner["id"]})

    def test_filters_cannot_select_another_landlord(self):
        for name in LIST_TOOLS:
            for arguments in [
                {"landlord_id": self.owners[1]["id"]},
                {"landlord_email": self.owners[1]["email"]},
                {"landlord_id": self.owners[0]["id"], "landlord_email": self.owners[1]["email"]},
            ]:
                with self.subTest(tool=name, arguments=arguments):
                    self.assertTrue(self.call(0, name, arguments).get("isError"))
            self.assertEqual(len(self.values(self.call(0, name, {
                "landlord_id": self.owners[0]["id"], "landlord_email": self.owners[0]["email"],
            }))), 1)

    def test_detail_tools_and_foreign_filters_are_scoped(self):
        for index in (0, 1):
            own, other = self.owners[index], self.owners[1 - index]
            for name, key in [
                ("get_property_overview", "property_id"), ("get_tenant_payment_history", "tenant_id"),
            ]:
                self.assertEqual(len(self.values(self.call(index, name, {key: own[key]}))), 1)
                self.assertTrue(self.call(index, name, {key: other[key]}).get("isError"))
            for name, key in [
                ("list_leases", "property_id"), ("list_leases", "tenant_id"),
                ("list_property_bills", "property_id"), ("list_rental_bills", "property_id"),
                ("list_rental_bills", "tenant_id"), ("list_payments", "tenant_id"),
            ]:
                self.assertEqual(self.values(self.call(index, name, {key: other[key]})), [])

    def test_other_account_cannot_reuse_mcp_session(self):
        for method in ("get", "post", "delete"):
            kwargs = {"json": {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}} if method == "post" else {}
            response = getattr(self.client, method)("/mcp", headers={
                "Authorization": f"Bearer {self.tokens[1]}", "mcp-session-id": self.session_ids[0],
            }, **kwargs)
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(len(self.values(self.call(0, "list_properties"))), 1)

    def test_invalid_expired_missing_and_non_landlord_tokens_rejected(self):
        secret = get_settings().jwt_secret_key
        payload = {"sub": str(self.owners[0]["user_id"]), "exp": 4102444800}
        invalid = [
            None, "garbage", jwt.encode(payload, "wrong-secret"),
            jwt.encode({**payload, "exp": 1}, secret),
            jwt.encode({"sub": payload["sub"]}, secret),
            jwt.encode({**payload, "sub": "not-an-id"}, secret),
            create_access_token("1"), create_access_token("9999"),
        ]
        for token in invalid:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = self.client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            })
            self.assertEqual(response.status_code, 401, response.text)

    def test_stdio_requires_explicit_token_and_http_never_falls_back_to_it(self):
        with patch.object(server, "MCP_TRANSPORT", "stdio"), patch.dict(
            os.environ, {"RENTAL_MGMT_MCP_TOKEN": self.tokens[0]},
        ):
            self.assertEqual(server.list_properties()[0]["landlord_id"], self.owners[0]["id"])
            with self.assertRaises(ValueError):
                server.list_properties(landlord_id=self.owners[1]["id"])
            request = Request({"type": "http", "headers": []})
            with patch.object(server.mcp, "get_context", return_value=SimpleNamespace(
                request_context=SimpleNamespace(request=request),
            )):
                with self.assertRaises(ValueError):
                    server.list_properties()
        with patch.dict(os.environ, {"RENTAL_MGMT_MCP_TOKEN": ""}):
            for name in TOOLS:
                arguments = {"tenant_id": 1} if name == "get_tenant_payment_history" else (
                    {"property_id": 1} if name == "get_property_overview" else {}
                )
                with self.assertRaises(ValueError):
                    getattr(server, name)(**arguments)

    def test_auth_cannot_be_disabled_and_metadata_matches_token_endpoint(self):
        with patch.object(server, "MCP_AUTH_ENABLED", False):
            with self.assertRaises(ValueError):
                server.secure_remote_mcp_app(None)
        metadata = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(metadata["grant_types_supported"], ["password"])
        self.assertTrue(metadata["token_endpoint"].endswith("/oauth/token"))

    def test_sse_messages_use_request_identity_and_bind_session_owner(self):
        async def exercise():
            sse_app = server.secure_remote_mcp_app(server.mcp.sse_app())
            outgoing = asyncio.Queue()
            incoming = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": b"", "more_body": False})
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": "/sse", "raw_path": b"/sse",
                "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8001),
                "client": ("127.0.0.1", 12345),
                "headers": [(b"host", b"127.0.0.1:8001"),
                            (b"authorization", f"Bearer {self.tokens[0]}".encode())],
            }

            async def next_data():
                while True:
                    message = await asyncio.wait_for(outgoing.get(), 5)
                    for line in message.get("body", b"").decode().splitlines():
                        if line.startswith("data: "):
                            return line[6:]

            task = asyncio.create_task(sse_app(scope, incoming.get, outgoing.put))
            try:
                endpoint = await next_data()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=sse_app), base_url="http://127.0.0.1:8001",
                ) as client:
                    async def post(index, message):
                        return await client.post(endpoint, json=message, headers={
                            "Authorization": f"Bearer {self.tokens[index]}",
                        })

                    initialize = {
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "sse-test", "version": "1"}},
                    }
                    self.assertEqual((await post(1, initialize)).status_code, 404)
                    self.assertEqual((await post(0, initialize)).status_code, 202)
                    self.assertIn("result", json.loads(await next_data()))
                    await post(0, {"jsonrpc": "2.0", "method": "notifications/initialized"})
                    response = await post(0, {
                        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "list_properties", "arguments": {}},
                    })
                    self.assertEqual(response.status_code, 202)
                    rows = self.values(json.loads(await next_data())["result"])
                    self.assertEqual([row["landlord_id"] for row in rows], [self.owners[0]["id"]])
            finally:
                await incoming.put({"type": "http.disconnect"})
                await asyncio.wait_for(task, 5)

        asyncio.run(exercise())

    def test_placeholder_signing_keys_are_rejected(self):
        for secret in ("change-me-in-production", "replace-this-with-a-long-random-secret", "short"):
            with patch.object(get_settings(), "jwt_secret_key", secret):
                with self.assertRaises(RuntimeError):
                    create_access_token("1")
                with self.assertRaises(RuntimeError):
                    decode_access_token(self.tokens[0])


if __name__ == "__main__":
    unittest.main()
