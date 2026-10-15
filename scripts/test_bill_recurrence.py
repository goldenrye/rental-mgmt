"""Run with python -m unittest discover -s scripts -p 'test_*.py'."""

import os
import unittest
from datetime import date
from decimal import Decimal

# Importing the API initializes its database; never use rental data in tests.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from pydantic import ValidationError
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import create_manual_bills, create_property_bills, manual_bill_due_dates
from app.models import LandlordProfile, Lease, LeaseTenant, PropertyBill, RentalProperty, TenantProfile, User
from app.schemas import ManualBillCreate, PropertyBillCreate


class BillRecurrenceTests(unittest.TestCase):
    def payload(self, schema=PropertyBillCreate, **changes):
        values = {
            "title": "Water",
            "amount_due": "60.00",
            "recurrence": "bi_monthly",
            "due_date": "2026-10-31",
            "period_start": "2026-10-01",
            "period_end": "2027-04-30",
        }
        if schema is PropertyBillCreate:
            values.update(property_id=1, bill_type="water")
        else:
            values.update(lease_id=1, tenant_ids=[1])
        values.update(changes)
        return schema(**values)

    def test_every_two_months_across_year_boundary(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            with self.subTest(schema=schema):
                self.assertEqual(
                    manual_bill_due_dates(self.payload(schema)),
                    [date(2026, 10, 31), date(2026, 12, 31), date(2027, 2, 28), date(2027, 4, 30)],
                )

    def test_leap_year_clamping_does_not_change_preferred_day(self):
        self.assertEqual(
            manual_bill_due_dates(self.payload(period_start="2027-12-01", period_end="2028-06-30")),
            [date(2027, 12, 31), date(2028, 2, 29), date(2028, 4, 30), date(2028, 6, 30)],
        )

    def test_quarterly_across_year_boundary(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            with self.subTest(schema=schema):
                self.assertEqual(
                    manual_bill_due_dates(self.payload(schema, recurrence="quarterly")),
                    [date(2026, 10, 31), date(2027, 1, 31), date(2027, 4, 30)],
                )

    def test_quarterly_clamps_february_without_day_drift(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            for year, february_day in ((2026, 28), (2027, 29)):
                with self.subTest(schema=schema, year=year):
                    self.assertEqual(
                        manual_bill_due_dates(self.payload(
                            schema, recurrence="quarterly", period_start=f"{year}-11-01",
                            period_end=f"{year + 1}-08-31",
                        )),
                        [date(year, 11, 30), date(year + 1, 2, february_day),
                         date(year + 1, 5, 31), date(year + 1, 8, 31)],
                    )

    def test_quarterly_short_period_and_period_anchor(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            self.assertEqual(
                manual_bill_due_dates(self.payload(
                    schema, recurrence="quarterly", period_end="2026-12-31",
                )),
                [date(2026, 10, 31)],
            )
            self.assertEqual(
                manual_bill_due_dates(self.payload(
                    schema, recurrence="quarterly", due_date="2026-11-15",
                    period_start="2026-10-01", period_end="2027-03-31",
                )),
                [date(2026, 10, 15), date(2027, 1, 15)],
            )

    def test_every_six_months_across_year_boundary(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            with self.subTest(schema=schema):
                self.assertEqual(
                    manual_bill_due_dates(
                        self.payload(schema, recurrence="semi_annually", period_end="2027-12-31")
                    ),
                    [date(2026, 10, 31), date(2027, 4, 30), date(2027, 10, 31)],
                )

    def test_six_month_recurrence_clamps_february_without_day_drift(self):
        for year, february_day in ((2026, 28), (2027, 29)):
            with self.subTest(year=year):
                self.assertEqual(
                    manual_bill_due_dates(
                        self.payload(
                            recurrence="semi_annually",
                            period_start=f"{year}-08-01",
                            period_end=f"{year + 1}-08-31",
                        )
                    ),
                    [date(year, 8, 31), date(year + 1, 2, february_day), date(year + 1, 8, 31)],
                )

    def test_six_month_recurrence_short_period(self):
        self.assertEqual(
            manual_bill_due_dates(self.payload(recurrence="semi_annually", period_end="2027-03-31")),
            [date(2026, 10, 31)],
        )

    def test_period_start_month_anchors_recurrence(self):
        self.assertEqual(
            manual_bill_due_dates(self.payload(due_date="2026-11-15", period_end="2027-03-31")),
            [date(2026, 10, 15), date(2026, 12, 15), date(2027, 2, 15)],
        )

    def test_short_period_generates_one_bill(self):
        for end in ("2026-10-31", "2026-11-30"):
            with self.subTest(end=end):
                self.assertEqual(
                    manual_bill_due_dates(self.payload(period_end=end)),
                    [date(2026, 10, 31)],
                )

    def test_recurring_period_validation(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            for changes in (
                {"period_start": None},
                {"period_end": None},
                {"period_end": "2026-09-30"},
            ):
                for recurrence in ("bi_monthly", "quarterly", "semi_annually"):
                    with self.subTest(schema=schema, recurrence=recurrence, changes=changes):
                        with self.assertRaises(ValidationError):
                            self.payload(schema, recurrence=recurrence, **changes)

    def test_existing_recurrences(self):
        for schema in (PropertyBillCreate, ManualBillCreate):
            for recurrence, expected in (
                ("one_time", [date(2026, 10, 31)]),
                ("custom_period", [date(2026, 10, 31)]),
                ("monthly", [date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)]),
                ("annually", [date(2026, 10, 31)]),
            ):
                with self.subTest(schema=schema, recurrence=recurrence):
                    self.assertEqual(
                        manual_bill_due_dates(self.payload(schema, recurrence=recurrence, period_end="2026-12-31")),
                        expected,
                    )

    def test_creation_persists_each_occurrence(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            user = User(email="test@example.com", full_name="Test", hashed_password="unused", role="landlord")
            landlord = LandlordProfile(user=user)
            property_ = RentalProperty(landlord=landlord, name="Test property", address="Test address")
            tenant = TenantProfile(landlord=landlord, full_name="Test tenant")
            lease = Lease(
                landlord=landlord,
                property=property_,
                start_date=date(2026, 1, 1),
                end_date=date(2027, 12, 31),
                monthly_rent=Decimal("100.00"),
            )
            lease.tenants.append(LeaseTenant(tenant=tenant, monthly_rent=Decimal("100.00"), deposit=Decimal("0.00")))
            db.add(lease)
            db.commit()

            for recurrence, expected_dates in (
                (
                    "bi_monthly",
                    [date(2026, 10, 31), date(2026, 12, 31), date(2027, 2, 28), date(2027, 4, 30)],
                ),
                ("semi_annually", [date(2026, 10, 31), date(2027, 4, 30)]),
                ("quarterly", [date(2026, 10, 31), date(2027, 1, 31), date(2027, 4, 30)]),
            ):
                property_bills = create_property_bills(
                    self.payload(property_id=property_.id, recurrence=recurrence), db, landlord
                )
                manual_bills = create_manual_bills(
                    self.payload(ManualBillCreate, lease_id=lease.id, tenant_ids=[tenant.id], recurrence=recurrence),
                    db,
                    landlord,
                )
                for bills in (property_bills, manual_bills):
                    with self.subTest(recurrence=recurrence, bill_type=type(bills[0]).__name__):
                        self.assertEqual(len(bills), len(expected_dates))
                        self.assertEqual([bill.due_date for bill in bills], expected_dates)
                        self.assertTrue(all(bill.id and bill.recurrence == recurrence for bill in bills))
                        self.assertTrue(all(bill.amount_due == Decimal("60.00") for bill in bills))
            db.expire_all()
            self.assertEqual(len(db.scalars(select(PropertyBill)).all()), 9)


if __name__ == "__main__":
    unittest.main()
