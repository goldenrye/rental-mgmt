"""Exercise confirmation with an isolated database, never actual billing data."""

import os
import unittest
from datetime import date
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, migrate_legacy_bill_uniqueness
from app.deps import get_landlord_profile
from app.main import app
from app.models import LandlordProfile, Lease, LeaseTenant, PropertyBill, RentalBill, RentalProperty, TenantProfile, User


class TenantChargePreviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        if getattr(self, "legacy_schema", False):
            with self.engine.begin() as connection:
                ddl = connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE name='rental_bills'"
                ).scalar_one()
                connection.exec_driver_sql("DROP TABLE rental_bills")
                ddl = ddl[:ddl.rfind(")")] + (
                    ", CONSTRAINT uq_bill_month UNIQUE (lease_id, tenant_id, bill_year, bill_month))"
                )
                connection.exec_driver_sql(ddl)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.landlord = LandlordProfile(
            user=User(email="preview@example.com", full_name="Test", hashed_password="unused", role="landlord")
        )
        self.property = RentalProperty(landlord=self.landlord, name="Test property", address="Test")
        self.tenants = [TenantProfile(landlord=self.landlord, full_name=name) for name in ("Alice", "Bob", "Casey")]
        self.db.add_all([self.property, *self.tenants])
        self.db.flush()
        self.lease = self.add_lease(self.property, self.tenants)
        self.bill = PropertyBill(
            property=self.property, landlord_id=self.landlord.id, title="Water",
            bill_type="water", amount_due=Decimal("100.00"), recurrence="one_time", due_date=date(2026, 11, 1),
        )
        self.db.add(self.bill)
        self.db.commit()
        self.url = f"/property-bills/{self.bill.id}"
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_landlord_profile] = lambda: self.landlord
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def add_lease(self, property_, tenants, start=date(2026, 1, 1), end=date(2028, 12, 31)):
        lease = Lease(
            landlord_id=self.landlord.id, property=property_, start_date=start,
            end_date=end, monthly_rent=Decimal("900.00"),
        )
        lease.tenants = [LeaseTenant(tenant=tenant, monthly_rent=Decimal("300.00")) for tenant in tenants]
        self.db.add(lease)
        self.db.flush()
        return lease

    def preview(self):
        response = self.client.get(self.url + "/tenant-bills-preview")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def generate(self, preview):
        return self.client.post(self.url + "/generate-tenant-bills", json={"preview_token": preview["preview_token"]})

    def test_preview_lists_equal_shares_and_creates_nothing(self):
        preview = self.preview()
        self.assertEqual([row["tenant_name"] for row in preview["tenants"]], ["Alice", "Bob", "Casey"])
        self.assertEqual([row["amount_due"] for row in preview["tenants"]], ["33.34", "33.33", "33.33"])
        self.assertEqual(preview["due_date"], "2026-11-30")
        self.assertEqual(list(self.db.scalars(select(RentalBill))), [])
        self.assertIsNone(self.bill.generated_at)
        self.assertEqual(self.preview()["preview_token"], preview["preview_token"])

    def test_confirmation_preserves_generation_behavior(self):
        preview = self.preview()
        response = self.generate(preview)
        self.assertEqual(response.status_code, 201, response.text)
        bills = response.json()
        self.assertEqual(len(bills), 3)
        self.assertEqual(sum(Decimal(bill["amount_due"]) for bill in bills), Decimal("100.00"))
        for bill, share in zip(bills, preview["tenants"]):
            self.assertEqual(bill["tenant_name"], share["tenant_name"])
            self.assertEqual(bill["amount_due"], share["amount_due"])
            self.assertEqual(bill["due_date"], preview["due_date"])
            self.assertTrue(bill["is_tenant_charge"])
            self.assertEqual(bill["bill_type"], "water")
            self.assertEqual(bill["source_property_bill_id"], self.bill.id)
        self.db.expire_all()
        self.assertEqual(self.bill.due_date, date(2026, 11, 1))
        self.assertIsNotNone(self.bill.generated_at)
        self.assertEqual(self.generate(preview).status_code, 409)
        self.assertEqual(len(list(self.db.scalars(select(RentalBill)))), 3)

    def test_changes_invalidate_confirmation(self):
        for field, value in (("amount_due", Decimal("200.00")), ("due_date", date(2026, 11, 15))):
            preview = self.preview()
            setattr(self.bill, field, value)
            self.db.commit()
            self.assertEqual(self.generate(preview).status_code, 409)
        preview = self.preview()
        self.lease.tenants.pop()
        self.db.commit()
        self.assertEqual(self.generate(preview).status_code, 409)
        self.assertEqual(list(self.db.scalars(select(RentalBill))), [])

    def test_empty_property_disallows_confirmation(self):
        self.lease.tenants.clear()
        self.db.commit()
        preview = self.preview()
        self.assertEqual(preview["tenants"], [])
        self.assertEqual(self.generate(preview).status_code, 400)
        self.assertIsNone(self.bill.generated_at)

    def test_ownership_and_confirmation_required(self):
        preview = self.preview()
        self.assertEqual(self.client.post(self.url + "/generate-tenant-bills").status_code, 422)
        app.dependency_overrides[get_landlord_profile] = lambda: LandlordProfile(id=self.landlord.id + 1)
        self.assertEqual(self.client.get(self.url + "/tenant-bills-preview").status_code, 404)
        self.assertEqual(self.generate(preview).status_code, 404)

    def test_only_property_tenants_are_included_once(self):
        self.add_lease(self.property, [self.tenants[0]])
        outsider = TenantProfile(landlord=self.landlord, full_name="Other tenant")
        other = RentalProperty(landlord=self.landlord, name="Other", address="Other")
        self.db.add_all([outsider, other])
        self.db.flush()
        self.add_lease(other, [outsider])
        self.add_lease(self.property, [outsider], start=date(2026, 11, 15))
        self.db.commit()
        self.assertEqual([row["tenant_name"] for row in self.preview()["tenants"]], ["Alice", "Bob", "Casey"])

    def test_month_end_preview_handles_leap_years(self):
        for original, expected in (
            (date(2027, 2, 1), "2027-02-28"),
            (date(2028, 2, 1), "2028-02-29"),
            (date(2027, 12, 1), "2027-12-31"),
        ):
            self.bill.due_date = original
            self.db.commit()
            self.assertEqual(self.preview()["due_date"], expected)


class LegacyTenantChargeTests(TenantChargePreviewTests):
    legacy_schema = True

    def setUp(self):
        super().setUp()
        self.assertTrue(migrate_legacy_bill_uniqueness(self.engine))

    def test_migration_is_idempotent(self):
        self.assertFalse(migrate_legacy_bill_uniqueness(self.engine))


class PopulatedLegacyBillTests(TenantChargePreviewTests):
    legacy_schema = True

    def test_existing_rent_and_payments_survive_and_charges_can_share_month(self):
        from app.billing import apply_payment, generate_bills_for_lease

        rent = generate_bills_for_lease(self.db, self.lease, date(2026, 11, 1))
        self.db.flush()
        payment = apply_payment(self.db, rent[0], Decimal("50.00"), None, "Existing payment")
        self.db.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE INDEX test_bill_due_date ON rental_bills(due_date)")
            before_bills = connection.exec_driver_sql("SELECT * FROM rental_bills ORDER BY id").fetchall()
            before_payments = connection.exec_driver_sql("SELECT * FROM payments ORDER BY id").fetchall()
        preview = self.preview()
        self.assertEqual(self.generate(preview).status_code, 409)
        self.db.rollback()
        self.assertTrue(migrate_legacy_bill_uniqueness(self.engine))
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("SELECT * FROM rental_bills ORDER BY id").fetchall(), before_bills)
            self.assertEqual(connection.exec_driver_sql("SELECT * FROM payments ORDER BY id").fetchall(), before_payments)
            self.assertIsNotNone(connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE name='test_bill_due_date'"
            ).first())
        self.assertEqual(self.generate(self.preview()).status_code, 201)
        second = PropertyBill(
            property=self.property, landlord_id=self.landlord.id, title="Electricity",
            bill_type="electricity", amount_due=Decimal("60.00"), recurrence="one_time",
            due_date=date(2026, 11, 15),
        )
        self.db.add(second)
        self.db.commit()
        self.url = f"/property-bills/{second.id}"
        self.assertEqual(self.generate(self.preview()).status_code, 201)
        self.assertEqual(len(list(self.db.scalars(select(RentalBill)))), 9)
        self.assertEqual(payment.bill_id, rent[0].id)
        self.assertEqual(len(generate_bills_for_lease(self.db, self.lease, date(2026, 11, 1))), 3)
        self.db.commit()
        self.assertEqual(len(list(self.db.scalars(select(RentalBill)))), 9)


if __name__ == "__main__":
    unittest.main()
