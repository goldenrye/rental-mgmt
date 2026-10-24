import asyncio
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import rent_scheduler
from app.billing import generate_bills_for_lease
from app.config import get_settings
from app.database import Base
from app.encryption import encode
from app.models import LandlordProfile, Lease, LeaseTenant, RentalBill, RentalProperty, RentBillingRun, TenantProfile, User


class RentSchedulerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{directory.name}/test.db", connect_args={"timeout": 20})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        secret = patch.object(get_settings(), "data_encryption_master_key", SecretStr(encode(os.urandom(32))))
        secret.start()
        self.addCleanup(secret.stop)
        self.ids = []
        with self.sessions() as db:
            for index in range(2):
                owner = LandlordProfile(encryption_enabled=bool(index), user=User(
                    email=f"scheduler{index}@example.test", full_name="Owner", hashed_password="unused", role="landlord",
                ))
                property_ = RentalProperty(landlord=owner, name="Home", address="Address")
                tenant = TenantProfile(landlord=owner, full_name="Tenant")
                db.add_all([property_, tenant])
                db.flush()
                lease = Lease(landlord_id=owner.id, property_id=property_.id,
                              start_date=date(2026, 1, 1), end_date=date(2027, 12, 31), monthly_rent=Decimal("310.00"))
                lease.tenants = [LeaseTenant(tenant_id=tenant.id, monthly_rent=Decimal("310.00"), deposit=0)]
                db.add(lease)
                db.flush()
                self.ids.append(lease.id)
            db.commit()

    def run_month(self, day=date(2026, 10, 24)):
        return rent_scheduler.reconcile_current_rent(self.sessions, day)

    def test_startup_after_first_and_repeated_runs_in_both_encryption_modes(self):
        self.assertEqual(self.run_month(), (2, 0))
        self.assertEqual(self.run_month(), (0, 0))
        with self.sessions() as db:
            bills = db.scalars(select(RentalBill)).all()
            self.assertEqual(len(bills), 2)
            self.assertEqual([b.amount_due for b in bills], [Decimal("310.00")] * 2)
            self.assertEqual({b.bill_month for b in bills}, {10})
            self.assertEqual(db.scalar(select(func.count()).select_from(RentBillingRun)), 2)
        self.assertEqual(self.run_month(date(2026, 11, 2)), (2, 0))

    def test_existing_paid_bill_is_unchanged_and_deleted_bills_stay_deleted(self):
        with self.sessions() as db:
            bills = generate_bills_for_lease(db, db.get(Lease, self.ids[0]), date(2026, 10, 1))
            bills[0].amount_paid = Decimal("310.00")
            bills[0].balance = 0
            bills[0].status = "paid"
            db.commit()
            before = bills[0].encrypted_data
        self.run_month()
        with self.sessions() as db:
            bill = db.scalar(select(RentalBill).where(RentalBill.lease_id == self.ids[0]))
            self.assertEqual(bill.encrypted_data, before)
            db.delete(bill)
            db.commit()
        self.run_month()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(RentalBill).where(RentalBill.lease_id == self.ids[0])))

    def test_failed_lease_rolls_back_and_retries_without_blocking_other_owner(self):
        def fail_first(db, lease, today, **kwargs):
            result = generate_bills_for_lease(db, lease, today, **kwargs)
            if lease.id == self.ids[0]:
                raise RuntimeError("simulated failure after flush")
            return result
        with patch.object(rent_scheduler, "generate_bills_for_lease", side_effect=fail_first):
            with self.assertLogs(rent_scheduler.logger, level="ERROR"):
                self.assertEqual(self.run_month(), (1, 1))
        with self.sessions() as db:
            self.assertIsNone(db.get(RentBillingRun, (self.ids[0], 2026, 10)))
            self.assertEqual(db.scalar(select(func.count()).select_from(RentalBill)), 1)
        self.assertEqual(self.run_month(), (1, 0))

    def test_concurrent_automatic_and_manual_generation_no_duplicates(self):
        barrier = threading.Barrier(2)
        def generate(automatic):
            with self.sessions() as db:
                lease = db.get(Lease, self.ids[0])
                barrier.wait(timeout=5)
                generate_bills_for_lease(db, lease, date(2026, 10, 24), automatic=automatic)
                db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(generate, flag) for flag in (True, False)]
            for future in futures:
                future.result(timeout=25)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RentalBill)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(RentBillingRun)), 1)

    def test_proration_and_out_of_month_leases(self):
        with self.sessions() as db:
            db.get(Lease, self.ids[0]).start_date = date(2026, 10, 16)
            db.get(Lease, self.ids[1]).end_date = date(2026, 9, 30)
            db.commit()
        self.assertEqual(self.run_month(), (1, 0))
        with self.sessions() as db:
            bill = db.scalar(select(RentalBill))
            self.assertEqual(bill.amount_due, Decimal("160.00"))
            self.assertEqual(bill.due_date, date(2026, 10, 16))

    def test_upgrade_recognizes_existing_rent_without_changing_it(self):
        self.run_month()
        with self.sessions() as db:
            before = {bill.id: bill.encrypted_data for bill in db.scalars(select(RentalBill))}
            db.execute(RentBillingRun.__table__.delete())
            db.commit()
        self.assertEqual(self.run_month(), (2, 0))
        with self.sessions() as db:
            self.assertEqual({bill.id: bill.encrypted_data for bill in db.scalars(select(RentalBill))}, before)

    def test_lease_deletion_removes_completion_markers(self):
        self.run_month()
        with self.sessions() as db:
            db.delete(db.get(Lease, self.ids[0]))
            db.commit()
            self.assertIsNone(db.get(RentBillingRun, (self.ids[0], 2026, 10)))

    def test_loop_survives_scan_failure_and_cancels_cleanly(self):
        async def run():
            with patch.object(rent_scheduler.asyncio, "to_thread", new=AsyncMock(side_effect=[RuntimeError(), (0, 0)])) as worker:
                with patch.object(rent_scheduler.asyncio, "sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])) as sleep:
                    with self.assertLogs(rent_scheduler.logger, level="ERROR"):
                        with self.assertRaises(asyncio.CancelledError):
                            await rent_scheduler.monthly_bill_scheduler()
                    self.assertEqual(worker.await_count, 2)
                    sleep.assert_awaited_with(3600)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
