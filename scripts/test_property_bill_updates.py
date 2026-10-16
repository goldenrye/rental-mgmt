"""Run with python -m unittest discover -s scripts -p 'test_*.py'."""

import os
import unittest
from datetime import date
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.deps import get_landlord_profile
from app.main import app
from app.models import LandlordProfile, PropertyBill, RentalProperty, User


class PropertyBillUpdateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.landlord = LandlordProfile(
            user=User(email="dates@example.com", full_name="Test", hashed_password="unused", role="landlord")
        )
        property_ = RentalProperty(landlord=self.landlord, name="Test", address="Test address")
        self.db.add(property_)
        self.db.flush()
        self.bills = [
            PropertyBill(
                property=property_,
                landlord_id=self.landlord.id,
                bill_type="water",
                title="Water",
                recurrence="semi_annually",
                due_date=due_date,
                amount_due=Decimal("60.00"),
                period_start=date(2026, 1, 1),
                period_end=date(2026, 12, 31),
            )
            for due_date in (date(2026, 1, 31), date(2026, 7, 31))
        ]
        self.db.add_all(self.bills)
        self.db.commit()
        self.bill_id = self.bills[0].id
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_landlord_profile] = lambda: self.landlord
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_due_date_edit_persists_without_changing_other_bills(self):
        response = self.client.patch(f"/property-bills/{self.bill_id}", json={"due_date": "2026-02-15"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["due_date"], "2026-02-15")
        self.db.expire_all()
        self.assertEqual(self.bills[0].due_date, date(2026, 2, 15))
        self.assertEqual(self.bills[1].due_date, date(2026, 7, 31))
        self.assertEqual(self.bills[0].amount_due, Decimal("60.00"))
        self.assertEqual(self.bills[0].recurrence, "semi_annually")
        self.assertEqual(self.bills[0].period_start, date(2026, 1, 1))
        self.assertEqual(self.bills[0].period_end, date(2026, 12, 31))
        listed = self.client.get("/property-bills").json()
        self.assertEqual(next(bill for bill in listed if bill["id"] == self.bill_id)["due_date"], "2026-02-15")

    def test_new_categories_can_be_created_updated_and_listed(self):
        for category in ("credit_card", "communication", "garbage", "hoa", "management"):
            with self.subTest(category=category):
                response = self.client.post("/property-bills", json={
                    "property_id": self.bills[0].property_id,
                    "bill_type": category,
                    "title": f"Test {category}",
                    "amount_due": "75.25",
                    "recurrence": "one_time",
                    "due_date": "2026-10-31",
                })
                self.assertEqual(response.status_code, 201, response.text)
                created = response.json()[0]
                self.assertEqual(created["bill_type"], category)
                updated = self.client.patch(
                    f"/property-bills/{self.bill_id}", json={"bill_type": category},
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["bill_type"], category)
                self.db.expire_all()
                self.assertEqual(self.db.get(PropertyBill, created["id"]).bill_type, category)
                self.assertEqual(self.bills[0].bill_type, category)
                listed = self.client.get("/property-bills").json()
                self.assertEqual(next(bill for bill in listed if bill["id"] == created["id"])["bill_type"], category)

    def test_unknown_category_is_rejected_on_create_and_update(self):
        response = self.client.post("/property-bills", json={
            "property_id": self.bills[0].property_id, "bill_type": "unknown",
            "title": "Invalid category", "amount_due": "10.00",
            "recurrence": "one_time", "due_date": "2026-10-31",
        })
        self.assertEqual(response.status_code, 422)
        response = self.client.patch(f"/property-bills/{self.bill_id}", json={"bill_type": "unknown"})
        self.assertEqual(response.status_code, 422)
        self.db.expire_all()
        self.assertEqual(self.bills[0].bill_type, "water")
        self.assertEqual(len(self.client.get("/property-bills").json()), 2)

    def test_invalid_dates_are_rejected(self):
        for value in ("", "not-a-date", "2026-02-30"):
            with self.subTest(value=value):
                response = self.client.patch(f"/property-bills/{self.bill_id}", json={"due_date": value})
                self.assertEqual(response.status_code, 422)
        self.db.expire_all()
        self.assertEqual(self.bills[0].due_date, date(2026, 1, 31))

    def test_amount_and_due_date_can_be_saved_together(self):
        response = self.client.patch(
            f"/property-bills/{self.bill_id}", json={"amount_due": "75.25", "due_date": "2026-02-15"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.bills[0].amount_due, Decimal("75.25"))
        self.assertEqual(self.bills[0].due_date, date(2026, 2, 15))
        self.assertEqual(self.bills[1].amount_due, Decimal("60.00"))
        self.assertEqual(self.bills[1].due_date, date(2026, 7, 31))

    def test_amount_can_be_saved_without_changing_due_date(self):
        response = self.client.patch(
            f"/property-bills/{self.bill_id}", json={"amount_due": "75.25", "due_date": "2026-01-31"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.bills[0].amount_due, Decimal("75.25"))
        self.assertEqual(self.bills[0].due_date, date(2026, 1, 31))

    def test_invalid_combined_update_does_not_change_either_field(self):
        for payload in (
            {"amount_due": "0", "due_date": "2026-02-15"},
            {"amount_due": "-1", "due_date": "2026-02-15"},
            {"amount_due": "75.255", "due_date": "2026-02-15"},
            {"amount_due": "75.25", "due_date": "2026-02-30"},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(f"/property-bills/{self.bill_id}", json=payload)
                self.assertEqual(response.status_code, 422)
                self.db.expire_all()
                self.assertEqual(self.bills[0].amount_due, Decimal("60.00"))
                self.assertEqual(self.bills[0].due_date, date(2026, 1, 31))

    def test_another_landlord_cannot_edit_due_date(self):
        app.dependency_overrides[get_landlord_profile] = lambda: LandlordProfile(id=self.landlord.id + 1)
        response = self.client.patch(f"/property-bills/{self.bill_id}", json={"due_date": "2026-02-15"})
        self.assertEqual(response.status_code, 404)
        self.db.expire_all()
        self.assertEqual(self.bills[0].due_date, date(2026, 1, 31))


if __name__ == "__main__":
    unittest.main()
