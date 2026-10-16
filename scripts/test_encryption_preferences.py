import os
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db
from app.encryption import EncryptionError, decrypt_record, key_table
from app.encryption_migration import initialize_encrypted_database, migrate_database
from app.encryption_preferences import set_encryption_preference
from app.main import app
from app.models import ENCRYPTED_MODELS, LandlordProfile, RentalProperty, RentalUnit, User
from app.security import hash_password
from scripts import test_encryption as encryption_fixture


class EncryptionPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = encryption_fixture.EncryptionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine

    def switch(self, enabled, identifier=1):
        with Session(self.engine) as db:
            set_encryption_preference(db, identifier, enabled)
            db.commit()

    def test_converts_all_existing_rows_and_future_writes_without_changing_other_owner(self):
        self.switch(False)
        with self.engine.connect() as connection:
            for model in ENCRYPTED_MODELS:
                rows = connection.execute(select(model.__table__)).mappings().all()
                for row in rows:
                    expected = "plain:v1:" if row["encryption_owner"] == "landlord:1" else "v1:"
                    self.assertTrue(row["encrypted_data"].startswith(expected))
        self.switch(True)
        with Session(self.engine) as db:
            for model in ENCRYPTED_MODELS:
                for row in db.scalars(select(model)):
                    self.assertTrue(row.encrypted_data.startswith("v1:"))
            unit = db.scalar(select(RentalUnit))
            unit.name = "After enable"
            db.add(RentalProperty(landlord_id=1, name="New secret", address="Private"))
            db.commit()
        self.switch(False)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(RentalUnit)).name, "After enable")
            self.assertEqual(db.get(RentalProperty, 1).address, "867 Secret Avenue")
            self.assertFalse(db.get(LandlordProfile, 1).encryption_enabled)
            self.assertTrue(db.get(LandlordProfile, 2).encryption_enabled)

    def test_default_off_account_needs_no_key(self):
        self.switch(False, 1)
        self.switch(False, 2)
        with patch.object(get_settings(), "data_encryption_master_key", SecretStr("")), \
             patch.object(get_settings(), "data_encryption_key_file", "/nonexistent/opt-in-key"):
            initialize_encrypted_database(self.engine)
            with Session(self.engine) as db:
                profile = LandlordProfile(user=User(email="plain@example.com", full_name="Plain", hashed_password="unused", role="landlord"))
                db.add(profile)
                db.flush()
                self.assertFalse(profile.encryption_enabled)
                property_ = RentalProperty(landlord_id=profile.id, name="Plain House", address="Readable")
                db.add(property_)
                db.flush()
                db.add(RentalUnit(property_id=property_.id, name="Plain Unit"))
                db.commit()
                self.assertEqual(property_.name, "Plain House")
                self.assertIn("Plain House", property_.encrypted_data)
                self.assertIsNone(db.scalar(select(key_table.c.owner).where(key_table.c.owner == f"landlord:{profile.id}")))

    def test_failed_conversion_rolls_back_every_row_and_preference(self):
        self.switch(False)
        from app import encryption_preferences
        original = encryption_preferences.encrypt_record
        def fail(connection, owner, table, *args, **kwargs):
            if table == "leases":
                raise EncryptionError("Injected failure")
            return original(connection, owner, table, *args, **kwargs)
        with patch.object(encryption_preferences, "encrypt_record", fail), self.assertRaises(EncryptionError):
            self.switch(True)
        with Session(self.engine) as db:
            self.assertFalse(db.get(LandlordProfile, 1).encryption_enabled)
            for model in ENCRYPTED_MODELS:
                for row in db.scalars(select(model).where(model.encryption_owner == "landlord:1")):
                    self.assertTrue(row.encrypted_data.startswith("plain:v1:"))

    def test_old_encrypted_accounts_keep_encryption_on(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE landlord_profiles DROP COLUMN encryption_enabled")
        initialize_encrypted_database(self.engine)
        with Session(self.engine) as db:
            self.assertTrue(all(profile.encryption_enabled for profile in db.scalars(select(LandlordProfile))))
            self.assertEqual(db.get(RentalProperty, 1).name, "Private Property")

    def test_legacy_plaintext_migration_does_not_opt_anyone_in_or_require_key(self):
        engine = self.fixture.legacy_database()
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE landlord_profiles DROP COLUMN encryption_enabled")
        with patch.object(get_settings(), "data_encryption_master_key", SecretStr("")), \
             patch.object(get_settings(), "data_encryption_key_file", "/nonexistent/opt-in-key"):
            migrate_database(engine)
            with Session(engine) as db:
                self.assertFalse(any(profile.encryption_enabled for profile in db.scalars(select(LandlordProfile))))
                self.assertEqual(db.get(RentalProperty, 1).name, "Private Property")
                self.assertIn("Private Property", db.get(RentalProperty, 1).encrypted_data)

    def test_enabled_account_rejects_plaintext_injection(self):
        with self.engine.connect() as connection, self.assertRaises(EncryptionError):
            decrypt_record(connection, "landlord:1", "rental_properties", 1, 'plain:v1:{"name":"Injected"}')

    def test_mcp_reads_both_storage_modes_with_the_same_results(self):
        from app import mcp_server
        with patch.object(mcp_server, "SessionLocal", sessionmaker(self.engine)), \
             patch.object(mcp_server, "resolve_landlord_id", return_value=1):
            encrypted = (mcp_server.list_properties(), mcp_server.list_tenants(), mcp_server.list_rental_bills())
            self.switch(False)
            plain = (mcp_server.list_properties(), mcp_server.list_tenants(), mcp_server.list_rental_bills())
            self.assertEqual(encrypted, plain)

    def test_database_default_is_off_even_for_raw_inserts(self):
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id,email,full_name,hashed_password,role) VALUES (99,'raw@example.com','Raw','unused','landlord')"))
            connection.execute(text("INSERT INTO landlord_profiles (id,user_id) VALUES (99,99)"))
        with Session(self.engine) as db:
            self.assertFalse(db.get(LandlordProfile, 99).encryption_enabled)


class EncryptionPreferenceApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        jwt_patch = patch.object(get_settings(), "jwt_secret_key", "opt-in-test-signing-key-0123456789abcdef")
        jwt_patch.start()
        self.addCleanup(jwt_patch.stop)
        key_patch = patch.object(get_settings(), "data_encryption_master_key", SecretStr("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="))
        key_patch.start()
        self.addCleanup(key_patch.stop)
        password = hash_password("owner-password")
        with Session(self.engine) as db:
            db.add_all([
                LandlordProfile(user=User(email="first@example.com", full_name="First", role="landlord", hashed_password=password)),
                LandlordProfile(user=User(email="second@example.com", full_name="Second", role="landlord", hashed_password=password)),
                User(email="admin@example.com", full_name="Admin", role="super_admin", hashed_password=password),
                User(email="tenant@example.com", full_name="Tenant", role="tenant", hashed_password=password),
            ])
            db.flush()
            db.add_all([RentalProperty(landlord_id=identifier, name=f"House {identifier}", address="Address") for identifier in (1, 2)])
            db.commit()
        def database():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = database
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def headers(self, email):
        response = self.client.post("/auth/login", json={"email": email, "password": "owner-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def test_owner_only_requires_password_and_reports_saved_setting(self):
        headers = self.headers("first@example.com")
        self.assertFalse(self.client.get("/me", headers=headers).json()["encryption_enabled"])
        self.assertEqual(self.client.put("/me/encryption", headers=headers, json={"enabled": True, "current_password": "wrong"}).status_code, 400)
        response = self.client.put("/me/encryption", headers=headers, json={
            "enabled": True, "current_password": "owner-password", "landlord_id": 2,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["encryption_enabled"])
        with Session(self.engine) as db:
            self.assertTrue(db.get(RentalProperty, 1).encrypted_data.startswith("v1:"))
            self.assertTrue(db.get(RentalProperty, 2).encrypted_data.startswith("plain:v1:"))
        self.assertEqual(self.client.get("/properties", headers=headers).json()[0]["name"], "House 1")
        response = self.client.put("/me/encryption", headers=headers, json={"enabled": False, "current_password": "owner-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["encryption_enabled"])

    def test_admin_tenant_and_anonymous_cannot_set_preference(self):
        payload = {"enabled": True, "current_password": "owner-password"}
        self.assertEqual(self.client.put("/me/encryption", json=payload).status_code, 401)
        for email in ("admin@example.com", "tenant@example.com"):
            self.assertEqual(self.client.put("/me/encryption", headers=self.headers(email), json=payload).status_code, 403)

    def test_missing_server_key_returns_error_without_enabling(self):
        headers = self.headers("first@example.com")
        with patch.object(get_settings(), "data_encryption_master_key", SecretStr("")), \
             patch.object(get_settings(), "data_encryption_key_file", "/nonexistent/opt-in-key"):
            response = self.client.put("/me/encryption", headers=headers, json={"enabled": True, "current_password": "owner-password"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.client.get("/me", headers=headers).json()["encryption_enabled"])
        self.assertEqual(self.client.get("/properties", headers=headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
