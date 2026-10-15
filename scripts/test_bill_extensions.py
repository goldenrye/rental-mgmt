"""Preview/confirm property bill extensions against an isolated database."""

import os
import unittest
from datetime import date

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.deps import get_landlord_profile
from app.main import app
from app.models import (
    LandlordProfile, PropertyBill, PropertyBillSeries, PropertyBillSeriesMember,
    RentalBill, RentalProperty, User,
)


class BillExtensionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.owner = LandlordProfile(user=User(
            email="extend@example.com", full_name="Owner", role="landlord", hashed_password="unused",
        ))
        self.property = RentalProperty(landlord=self.owner, name="House", address="Test")
        self.db.add(self.property)
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_landlord_profile] = lambda: self.owner
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self, **changes):
        payload = dict(
            property_id=self.property.id, bill_type="water", title="Water",
            recurrence="quarterly", amount_due="60.00", due_date="2030-10-31",
            period_start="2030-10-01", period_end="2031-04-30",
        )
        payload.update(changes)
        response = self.client.post("/property-bills", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def preview(self, source_id, through="2032-04-30", **changes):
        response = self.client.post(
            f"/property-bills/{source_id}/extension-preview", json={"through": through, **changes},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def confirm(self, source_id, preview):
        return self.client.post(f"/property-bills/{source_id}/extend", json={
            "through": preview["through"], "due_day": preview["due_day"],
            "preview_token": preview["preview_token"],
        })

    def count(self, model=PropertyBill):
        return self.db.scalar(select(func.count()).select_from(model))

    def test_preview_lists_existing_and_new_without_writes(self):
        bills = self.create()
        original = self.client.get("/property-bills").json()
        preview = self.preview(bills[1]["id"])
        self.assertEqual(preview["existing_future_bills"], original)
        self.assertEqual([bill["due_date"] for bill in preview["new_bills"]], [
            "2031-07-31", "2031-10-31", "2032-01-31", "2032-04-30",
        ])
        self.assertEqual(preview["total_new_amount"], "240.00")
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.count(PropertyBillSeries), 1)
        self.assertEqual(self.client.get("/property-bills").json(), original)
        self.assertEqual(preview, self.preview(bills[1]["id"]))

    def test_confirmation_preserves_existing_and_creates_only_property_bills(self):
        bills = self.create()
        source_id = bills[0]["id"]
        self.client.patch(f"/property-bills/{source_id}", json={"status": "paid"})
        original = self.client.get("/property-bills").json()
        preview = self.preview(source_id)
        response = self.confirm(source_id, preview)
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertEqual(len(created), 4)
        for bill in created:
            self.assertEqual(bill["status"], "unpaid")
            self.assertIsNone(bill["paid_at"])
            self.assertIsNone(bill["generated_at"])
            self.assertEqual(bill["amount_due"], "60.00")
            self.assertEqual(bill["period_start"], "2031-05-01")
        self.assertEqual(self.count(RentalBill), 0)
        self.assertEqual(self.client.get("/property-bills").json()[:3], original)
        self.assertIn(self.confirm(source_id, preview).status_code, (400, 409))
        self.assertEqual(self.count(), 7)
        # Any original or newly generated member can extend the same series.
        next_preview = self.preview(created[-1]["id"], "2032-10-31")
        self.assertEqual([bill["due_date"] for bill in next_preview["new_bills"]], ["2032-07-31", "2032-10-31"])
        other_preview = self.preview(bills[2]["id"], "2032-10-31")
        self.assertEqual(self.confirm(created[-1]["id"], next_preview).status_code, 201)
        self.assertIn(self.confirm(bills[2]["id"], other_preview).status_code, (400, 409))
        self.assertEqual(self.count(), 9)

    def test_stale_changes_require_new_preview(self):
        bills = self.create()
        source_id = bills[0]["id"]
        preview = self.preview(source_id)
        self.client.patch(f"/property-bills/{source_id}", json={"amount_due": "75.00"})
        self.assertEqual(self.confirm(source_id, preview).status_code, 409)
        preview = self.preview(source_id)
        self.assertEqual(preview["total_new_amount"], "300.00")
        self.client.patch(f"/property-bills/{bills[1]['id']}", json={"due_date": "2031-07-15"})
        self.assertEqual(self.confirm(source_id, preview).status_code, 409)
        preview = self.preview(source_id)
        self.assertNotIn("2031-07-31", [bill["due_date"] for bill in preview["new_bills"]])
        self.assertEqual(self.count(), 3)

    def test_all_recurrences_and_leap_years(self):
        for recurrence, expected in [
            ("monthly", ["2031-12-31", "2032-01-31", "2032-02-29", "2032-03-31", "2032-04-30", "2032-05-31"]),
            ("bi_monthly", ["2032-01-31", "2032-03-31", "2032-05-31"]),
            ("quarterly", ["2032-02-29", "2032-05-31"]),
            ("semi_annually", ["2032-05-31"]),
            ("annually", []),
        ]:
            with self.subTest(recurrence=recurrence):
                bill = self.create(recurrence=recurrence, period_start="2031-11-01",
                                   period_end="2031-11-30", due_date="2031-11-30")[0]
                preview = self.preview(bill["id"], "2032-05-31", due_day=31)
                self.assertEqual([item["due_date"] for item in preview["new_bills"]], expected)
        bill = self.create(recurrence="annually", period_start="2032-02-01",
                           period_end="2032-12-31", due_date="2032-02-29")[0]
        preview = self.preview(bill["id"], "2036-12-31")
        self.assertEqual([item["due_date"] for item in preview["new_bills"]],
                         ["2033-02-28", "2034-02-28", "2035-02-28", "2036-02-29"])

    def test_partial_month_end_does_not_skip_later_due_day(self):
        source_id = self.create(recurrence="monthly", period_end="2030-10-31")[0]["id"]
        preview = self.preview(source_id, "2030-12-15")
        self.assertEqual([bill["due_date"] for bill in preview["new_bills"]], ["2030-11-30"])
        self.assertEqual(self.confirm(source_id, preview).status_code, 201)
        next_preview = self.preview(source_id, "2031-01-31")
        self.assertEqual([bill["due_date"] for bill in next_preview["new_bills"]], ["2030-12-31", "2031-01-31"])

    def test_clamped_selected_bill_keeps_original_due_day(self):
        source_id = self.create(
            recurrence="monthly", period_start="2031-02-01", period_end="2031-02-28",
            due_date="2031-01-31",
        )[0]["id"]
        preview = self.preview(source_id, "2031-03-31")
        self.assertEqual(preview["due_day"], 31)
        self.assertEqual(preview["new_bills"][0]["due_date"], "2031-03-31")

    def test_existing_future_preview_does_not_include_past_bills(self):
        source_id = self.create(period_start="2020-01-01", period_end="2020-12-31", due_date="2020-01-31")[0]["id"]
        self.assertEqual(self.preview(source_id, "2021-12-31")["existing_future_bills"], [])

    def test_deleted_member_invalidates_preview(self):
        bills = self.create()
        preview = self.preview(bills[0]["id"])
        self.assertEqual(self.client.delete(f"/property-bills/{bills[1]['id']}").status_code, 204)
        self.assertEqual(self.confirm(bills[0]["id"], preview).status_code, 409)

    def test_invalid_requests_and_empty_preview(self):
        source_id = self.create()[0]["id"]
        for payload in (
            {}, {"through": "2031-04-30"}, {"through": "bad"}, {"through": "2050-01-01"},
            {"through": "2032-01-01", "due_day": 0}, {"through": "2032-01-01", "due_day": 32},
        ):
            response = self.client.post(f"/property-bills/{source_id}/extension-preview", json=payload)
            self.assertIn(response.status_code, (400, 422))
        preview = self.preview(source_id, "2031-05-31")
        self.assertEqual(preview["new_bills"], [])
        self.assertEqual(self.confirm(source_id, preview).status_code, 400)
        self.assertEqual(self.client.post(f"/property-bills/{source_id}/extend",
                                         json={"through": "2032-01-01"}).status_code, 422)
        for recurrence in ("one_time", "custom_period"):
            bill_id = self.create(recurrence=recurrence)[0]["id"]
            self.assertEqual(self.client.post(f"/property-bills/{bill_id}/extension-preview",
                                             json={"through": "2032-01-01"}).status_code, 400)

    def test_landlord_isolation(self):
        source_id = self.create()[0]["id"]
        preview = self.preview(source_id)
        app.dependency_overrides[get_landlord_profile] = lambda: LandlordProfile(id=self.owner.id + 1)
        self.assertEqual(self.client.post(f"/property-bills/{source_id}/extension-preview",
                                         json={"through": "2032-04-30"}).status_code, 404)
        self.assertEqual(self.confirm(source_id, preview).status_code, 404)
        self.assertEqual(self.count(), 3)

    def test_separate_identical_series_remain_separate(self):
        first = self.create()
        second = self.create()
        preview = self.preview(first[0]["id"])
        self.assertEqual([bill["id"] for bill in preview["existing_future_bills"]], [bill["id"] for bill in first])
        self.assertEqual(self.confirm(first[0]["id"], preview).status_code, 201)
        self.assertEqual(self.preview(second[0]["id"])["current_end"], "2031-04-30")

    def test_legacy_preview_is_read_only_then_tracks_members(self):
        legacy = []
        for due in (date(2030, 10, 31), date(2031, 1, 31), date(2031, 4, 30)):
            bill = PropertyBill(
                landlord_id=self.owner.id, property_id=self.property.id, title="Legacy",
                bill_type="water", recurrence="quarterly", amount_due=60,
                period_start=date(2030, 10, 1), period_end=date(2031, 4, 30), due_date=due,
            )
            self.db.add(bill)
            legacy.append(bill)
        self.db.commit()
        preview = self.preview(legacy[1].id)
        self.assertIsNotNone(preview["warning"])
        self.assertEqual(len(preview["existing_future_bills"]), 3)
        self.assertEqual(self.count(PropertyBillSeries), 0)
        self.assertEqual(self.count(PropertyBillSeriesMember), 0)
        self.assertEqual(self.confirm(legacy[1].id, preview).status_code, 201)
        self.assertEqual(self.count(PropertyBillSeries), 1)
        self.assertEqual(self.count(PropertyBillSeriesMember), 7)
        self.assertEqual(self.preview(legacy[0].id, "2033-01-31")["current_end"], "2032-04-30")

    def test_ambiguous_legacy_batches_are_not_silently_combined(self):
        for _ in range(2):
            self.db.add(PropertyBill(
                landlord_id=self.owner.id, property_id=self.property.id, title="Duplicate",
                bill_type="water", recurrence="monthly", amount_due=60,
                period_start=date(2030, 1, 1), period_end=date(2030, 1, 31), due_date=date(2030, 1, 31),
            ))
        self.db.commit()
        source_id = self.db.scalar(select(PropertyBill.id))
        response = self.client.post(f"/property-bills/{source_id}/extension-preview",
                                    json={"through": "2030-12-31"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.count(PropertyBillSeries), 0)


if __name__ == "__main__":
    unittest.main()
