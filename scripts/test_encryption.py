import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text, UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import get_settings
from app.database import Base
from app.encrypted_models import read_values
from app.encryption import EncryptionError, data_key, decrypt_record, encode, key_table
from app.encryption_migration import initialize_encrypted_database, migrate_database, verify_database
from app.models import (
    ENCRYPTED_MODELS, LandlordProfile, Lease, LeaseTenant, Payment, PropertyBill,
    RentalBill, RentalProperty, RentalUnit, TenantProfile, User,
)
from app.account_access import revoke_all_sessions
from app.security import hash_password
from scripts.manage_encryption import create_key_file


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.secret = SecretStr(encode(os.urandom(32)))
        self.patch = patch.object(get_settings(), "data_encryption_master_key", self.secret)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        initialize_encrypted_database(self.engine)
        with Session(self.engine) as db:
            owners = [LandlordProfile(encryption_enabled=True, user=User(
                email=f"owner{index}@example.com", full_name=f"Account {index}",
                hashed_password="unused", role="landlord",
            )) for index in range(2)]
            db.add_all(owners)
            db.flush()
            self.owner_ids = [owner.id for owner in owners]
            properties = [RentalProperty(landlord_id=owner.id, name="Private Property",
                                         address="867 Secret Avenue", description="Confidential description") for owner in owners]
            tenants = [TenantProfile(landlord_id=owner.id, full_name="Sensitive Tenant",
                                     email="private-contact@example.com", phone="555-9988", notes="Private contact notes") for owner in owners]
            db.add_all([*properties, *tenants])
            db.flush()
            db.add(RentalUnit(property_id=properties[0].id, name="Private Unit", description="Unit secret"))
            lease = Lease(landlord_id=owners[0].id, property_id=properties[0].id,
                          start_date=date(2026, 1, 1), end_date=date(2027, 1, 1),
                          monthly_rent=Decimal("1981.27"), notes="Private lease")
            db.add(lease)
            db.flush()
            db.add(LeaseTenant(lease_id=lease.id, tenant_id=tenants[0].id,
                               monthly_rent=Decimal("1981.27"), deposit=Decimal("2993.89")))
            expense = PropertyBill(landlord_id=owners[0].id, property_id=properties[0].id,
                                   bill_type="water", title="Secret Expense", recurrence="monthly",
                                   due_date=date(2026, 10, 31), amount_due=Decimal("187.43"), notes="Expense secret")
            bill = RentalBill(landlord_id=owners[0].id, lease_id=lease.id, tenant_id=tenants[0].id,
                              bill_year=2026, bill_month=10, due_date=date(2026, 10, 31),
                              title="Secret Rent", amount_due=Decimal("1981.27"), amount_paid=Decimal("123.45"),
                              balance=Decimal("1857.82"), status="partial")
            db.add_all([expense, bill])
            db.flush()
            db.add(Payment(bill_id=bill.id, landlord_id=owners[0].id, tenant_id=tenants[0].id,
                           amount=Decimal("123.45"), paid_at=datetime.now(timezone.utc), note="Secret Payment"))
            db.commit()

    def test_all_sensitive_fields_round_trip_and_are_absent_from_sql(self):
        counts = verify_database(self.engine)
        self.assertTrue(all(count > 0 for count in counts.values()))
        with Session(self.engine) as db:
            self.assertEqual(db.get(RentalProperty, 1).address, "867 Secret Avenue")
            self.assertEqual(db.get(TenantProfile, 1).email, "private-contact@example.com")
            self.assertEqual(db.get(Lease, 1).monthly_rent, Decimal("1981.27"))
            self.assertEqual(db.get(LeaseTenant, 1).deposit, Decimal("2993.89"))
            self.assertEqual(db.get(RentalBill, 1).balance, Decimal("1857.82"))
            self.assertEqual(db.get(Payment, 1).note, "Secret Payment")
        with self.engine.connect() as connection:
            dump = "\n".join(connection.connection.driver_connection.iterdump())
            for value in ("867 Secret Avenue", "Sensitive Tenant", "1981.27", "2993.89",
                          "Secret Expense", "private-contact@example.com", "Secret Rent", "Secret Payment"):
                self.assertNotIn(value, dump)
            for model in ENCRYPTED_MODELS:
                columns = {item["name"] for item in inspect(connection).get_columns(model.__tablename__)}
                self.assertFalse(columns & set(model.__sensitive_fields__))

    def test_distinct_keys_and_owner_row_binding(self):
        with self.engine.connect() as connection:
            owners = [f"landlord:{identifier}" for identifier in self.owner_ids]
            self.assertNotEqual(data_key(connection, owners[0]), data_key(connection, owners[1]))
            rows = connection.execute(select(RentalProperty.__table__).order_by(RentalProperty.id)).mappings().all()
            self.assertNotEqual(rows[0]["encrypted_data"], rows[1]["encrypted_data"])
            for owner, identifier in [(owners[1], 1), (owners[0], 2)]:
                with self.assertRaises(EncryptionError):
                    decrypt_record(connection, owner, "rental_properties", identifier, rows[0]["encrypted_data"])

    def test_edits_reencrypt_and_rollback_restores_plaintext_view(self):
        with Session(self.engine) as db:
            property_ = db.get(RentalProperty, 1)
            original = property_.encrypted_data
            db.commit()
            property_.name = "Edited secret"
            db.commit()
            self.assertEqual(property_.name, "Edited secret")
            self.assertNotEqual(original, property_.encrypted_data)
            previous = property_.encrypted_data
            property_.name = "Edited secret"
            db.commit()
            self.assertNotEqual(previous, property_.encrypted_data)
            property_.name = "Should roll back"
            db.flush()
            db.rollback()
            self.assertEqual(property_.name, "Edited secret")

    def test_wrong_missing_key_and_corrupted_payload_fail_closed(self):
        with patch.object(get_settings(), "data_encryption_master_key", SecretStr(encode(os.urandom(32)))):
            with self.assertRaises(EncryptionError):
                initialize_encrypted_database(self.engine)
            with Session(self.engine) as db, self.assertRaises(EncryptionError):
                _ = db.get(RentalProperty, 1).name
        with patch.object(get_settings(), "data_encryption_master_key", SecretStr("")), \
             patch.object(get_settings(), "data_encryption_key_file", "/nonexistent/encryption-key"):
            with self.assertRaises(EncryptionError):
                initialize_encrypted_database(self.engine)
        for invalid in ("", "plaintext", "v1:invalid"):
            with self.engine.begin() as connection:
                connection.execute(text("UPDATE rental_properties SET encrypted_data=:value WHERE id=1"), {"value": invalid})
            with Session(self.engine) as db, self.assertRaises(EncryptionError):
                _ = db.get(RentalProperty, 1).name

    def test_deleted_wrapped_key_is_not_silently_replaced(self):
        with self.engine.begin() as connection:
            connection.execute(key_table.delete().where(key_table.c.owner == "landlord:1"))
        with self.engine.begin() as connection, self.assertRaises(EncryptionError):
            data_key(connection, "landlord:1", create=True)
        with self.assertRaises(EncryptionError):
            initialize_encrypted_database(self.engine)

    def test_unit_uniqueness_and_renames(self):
        with Session(self.engine) as db:
            db.add(RentalUnit(property_id=1, name="Private Unit"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            unit = db.scalar(select(RentalUnit))
            unit.name = "Renamed Unit"
            db.commit()
            db.add(RentalUnit(property_id=1, name="Private Unit"))
            db.commit()
            self.assertEqual(len(db.scalars(select(RentalUnit)).all()), 2)

    def test_concurrent_encrypted_edits_do_not_overwrite_each_other(self):
        with Session(self.engine) as first, Session(self.engine) as second:
            first_row = first.get(RentalProperty, 1)
            second_row = second.get(RentalProperty, 1)
            first_row.name = "First edit"
            first.commit()
            second_row.address = "Second edit"
            with self.assertRaises(StaleDataError):
                second.commit()
            second.rollback()
            self.assertEqual(second_row.name, "First edit")
            self.assertEqual(second_row.address, "867 Secret Avenue")

    def test_password_reset_preserves_data_key_and_decryption(self):
        with self.engine.connect() as connection:
            previous = data_key(connection, "landlord:1")
        with Session(self.engine) as db:
            user = db.get(User, 1)
            user.hashed_password = hash_password("reset-password")
            revoke_all_sessions(db, user)
            db.commit()
            self.assertEqual(db.get(RentalProperty, 1).name, "Private Property")
        with self.engine.connect() as connection:
            self.assertEqual(data_key(connection, "landlord:1"), previous)

    def legacy_database(self, url="sqlite:///:memory:", connect_args=None):
        metadata = MetaData()
        by_table = {model.__tablename__: model for model in ENCRYPTED_MODELS}
        for table in Base.metadata.sorted_tables:
            if table.name == key_table.name:
                continue
            model = by_table.get(table.name)
            columns = [
                Column(column.name, column.type, *[ForeignKey(fk.target_fullname) for fk in column.foreign_keys],
                       primary_key=column.primary_key, nullable=column.nullable)
                for column in table.columns if column.name not in {"encrypted_data", "encryption_owner", "name_index"}
            ]
            if model:
                columns += [Column(name, Numeric(12, 2) if field.money else Text, nullable=not field.required)
                            for name, field in model.__sensitive_fields__.items()]
            legacy = Table(table.name, metadata, *columns)
            if table.name == "rental_units":
                legacy.append_constraint(UniqueConstraint("property_id", "name", name="uq_property_unit_name"))
            else:
                for constraint in table.constraints:
                    if isinstance(constraint, UniqueConstraint):
                        legacy.append_constraint(UniqueConstraint(
                            *[column.name for column in constraint.columns], name=constraint.name,
                        ))
        engine = create_engine(url, connect_args=connect_args or {})
        self.addCleanup(engine.dispose)
        metadata.create_all(engine)
        with Session(self.engine) as source, engine.begin() as target:
            for table in metadata.sorted_tables:
                if table.name in by_table:
                    model = by_table[table.name]
                    for instance in source.scalars(select(model)):
                        row = {column.name: getattr(instance, column.name) for column in model.__table__.columns
                               if column.name not in {"encrypted_data", "encryption_owner", "name_index"}}
                        row.update(read_values(instance))
                        target.execute(table.insert().values(**row))
                else:
                    for row in source.execute(select(Base.metadata.tables[table.name])).mappings():
                        target.execute(table.insert().values(**row))
        return engine

    def test_legacy_migration_preserves_values_and_is_idempotent(self):
        engine = self.legacy_database()
        with self.assertRaises(EncryptionError):
            initialize_encrypted_database(engine)
        counts = migrate_database(engine)
        self.assertEqual(counts["rental_properties"], 2)
        self.assertEqual(migrate_database(engine), {})
        self.assertEqual(verify_database(engine), counts)
        with Session(engine) as db:
            self.assertEqual(db.get(RentalProperty, 1).address, "867 Secret Avenue")
            self.assertEqual(db.get(LeaseTenant, 1).monthly_rent, Decimal("1981.27"))
            self.assertEqual(db.get(Payment, 1).amount, Decimal("123.45"))
            self.assertEqual(db.get(RentalBill, 1).amount_paid, Decimal("123.45"))
            self.assertEqual(db.get(Payment, 1).bill_id, 1)

    def test_migration_failure_rolls_back_schema_data_and_keys(self):
        engine = self.legacy_database()
        from app import encryption_migration
        original = encryption_migration.encrypted_row
        def failing(connection, model, row):
            if model is Lease:
                raise RuntimeError("Injected migration failure")
            return original(connection, model, row)
        with patch.object(encryption_migration, "encrypted_row", failing), self.assertRaises(RuntimeError):
            migrate_database(engine)
        with engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT name FROM rental_properties WHERE id=1")), "Private Property")
            self.assertNotIn("encrypted_data", {column["name"] for column in inspect(connection).get_columns("tenant_profiles")})
            self.assertNotIn(key_table.name, inspect(connection).get_table_names())

    def test_sqlite_compaction_removes_old_plaintext_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            engine = self.legacy_database(f"sqlite:///{path}")
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                connection.commit()
            with engine.begin() as connection:
                connection.execute(text("UPDATE rental_properties SET address='867 Secret Avenue' WHERE id=1"))
            migrate_database(engine)
            engine.dispose()
            for file in [path, Path(str(path) + "-wal")]:
                if file.exists():
                    self.assertNotIn(b"867 Secret Avenue", file.read_bytes())

    def test_key_file_is_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keys" / "data.key"
            create_key_file(path)
            original = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                create_key_file(path)
            self.assertEqual(path.read_bytes(), original)


@unittest.skipUnless(os.getenv("TEST_ENCRYPTION_POSTGRES_URL"), "PostgreSQL test server not configured")
class PostgresEncryptionTests(unittest.TestCase):
    def test_migration_and_orm_round_trip_in_isolated_schema(self):
        fixture = EncryptionTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        url = os.environ["TEST_ENCRYPTION_POSTGRES_URL"]
        admin_engine = create_engine(url)
        self.addCleanup(admin_engine.dispose)
        schema = "encryption_test_" + uuid4().hex
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        def cleanup():
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        self.addCleanup(cleanup)
        engine = fixture.legacy_database(url, connect_args={"options": f"-csearch_path={schema}"})
        self.addCleanup(engine.dispose)
        counts = migrate_database(engine)
        self.assertEqual(verify_database(engine), counts)
        self.assertEqual(migrate_database(engine), {})
        with Session(engine) as db:
            self.assertEqual(db.get(RentalProperty, 1).address, "867 Secret Avenue")
            bill = db.get(RentalBill, 1)
            bill.amount_due = Decimal("2345.67")
            db.commit()
            self.assertEqual(bill.amount_due, Decimal("2345.67"))


if __name__ == "__main__":
    unittest.main()
