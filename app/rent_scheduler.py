"""Recover current-month rent generation on startup and retry failed leases."""

import asyncio
import logging
from datetime import date

from sqlalchemy import select

from app.billing import generate_bills_for_lease, month_window
from app.database import SessionLocal
from app.models import Lease, RentBillingRun

logger = logging.getLogger(__name__)
RETRY_SECONDS = 60 * 60


def reconcile_current_rent(session_factory=SessionLocal, billing_date=None) -> tuple[int, int]:
    today = billing_date or date.today()
    start, end = month_window(today)
    with session_factory() as db:
        completed = select(RentBillingRun.lease_id).where(
            RentBillingRun.lease_id == Lease.id,
            RentBillingRun.bill_year == today.year,
            RentBillingRun.bill_month == today.month,
        ).exists()
        lease_ids = list(db.scalars(select(Lease.id).where(
            Lease.start_date <= end, Lease.end_date >= start, ~completed,
        ).order_by(Lease.id)))
    succeeded = failed = 0
    for lease_id in lease_ids:
        try:
            with session_factory() as db:
                lease = db.get(Lease, lease_id)
                if lease is None:
                    continue
                generate_bills_for_lease(db, lease, today, automatic=True)
                db.commit()
                succeeded += 1
        except Exception as exc:
            # Avoid exception text/SQL parameters: these may include sensitive data.
            logger.error("Automatic rent failed: lease_id=%s month=%s error=%s; retry in %ss",
                         lease_id, start, type(exc).__name__, RETRY_SECONDS)
            failed += 1
    if succeeded or failed:
        logger.info("Automatic rent reconciliation month=%s completed=%s failed=%s", start, succeeded, failed)
    return succeeded, failed


async def monthly_bill_scheduler() -> None:
    while True:
        try:
            # SQLAlchemy sessions and encryption are synchronous; don't block API requests.
            await asyncio.to_thread(reconcile_current_rent)
        except Exception as exc:
            logger.error("Automatic rent scan failed: error=%s; retry in %ss", type(exc).__name__, RETRY_SECONDS)
        await asyncio.sleep(RETRY_SECONDS)
