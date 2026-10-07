from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BillStatus, Lease, Payment, RentalBill


TWOPLACES = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def monthly_due_date(year: int, month: int, preferred_day: int) -> date:
    last_day = monthrange(year, month)[1]
    return date(year, month, min(preferred_day, last_day))


def iter_months(start: date, end: date) -> list[tuple[int, int]]:
    cursor_year = start.year
    cursor_month = start.month
    months: list[tuple[int, int]] = []
    while (cursor_year, cursor_month) <= (end.year, end.month):
        months.append((cursor_year, cursor_month))
        if cursor_month == 12:
            cursor_year += 1
            cursor_month = 1
        else:
            cursor_month += 1
    return months


def split_rent(monthly_rent: Decimal, tenant_count: int) -> Decimal:
    if tenant_count <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lease must have at least one tenant")
    return money(monthly_rent / Decimal(tenant_count))


def refresh_bill_status(bill: RentalBill) -> None:
    bill.amount_paid = money(bill.amount_paid)
    bill.balance = money(bill.amount_due - bill.amount_paid)
    if bill.balance <= Decimal("0.00"):
        bill.status = BillStatus.paid.value
        bill.balance = Decimal("0.00")
    elif bill.amount_paid > Decimal("0.00"):
        bill.status = BillStatus.partial.value
    else:
        bill.status = BillStatus.open.value


def generate_bills_for_lease(db: Session, lease: Lease, through: Optional[date] = None) -> list[RentalBill]:
    through = through or date.today()
    if lease.start_date > through:
        return []

    generation_end = min(lease.end_date, through)
    tenant_ids = [lease_tenant.tenant_id for lease_tenant in lease.tenants]
    if not tenant_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lease must have at least one tenant")

    amount_due = split_rent(lease.monthly_rent, len(tenant_ids))
    bills: list[RentalBill] = []

    for bill_year, bill_month in iter_months(lease.start_date, generation_end):
        for tenant_id in tenant_ids:
            existing = db.scalar(
                select(RentalBill).where(
                    RentalBill.lease_id == lease.id,
                    RentalBill.tenant_id == tenant_id,
                    RentalBill.bill_year == bill_year,
                    RentalBill.bill_month == bill_month,
                )
            )
            if existing:
                bills.append(existing)
                continue

            bill = RentalBill(
                landlord_id=lease.landlord_id,
                lease_id=lease.id,
                tenant_id=tenant_id,
                bill_year=bill_year,
                bill_month=bill_month,
                due_date=monthly_due_date(bill_year, bill_month, lease.start_date.day),
                amount_due=amount_due,
                amount_paid=Decimal("0.00"),
                balance=amount_due,
                status=BillStatus.open.value,
            )
            db.add(bill)
            bills.append(bill)

    db.flush()
    return bills


def apply_payment(
    db: Session, bill: RentalBill, amount: Decimal, paid_at: Optional[datetime], note: Optional[str]
) -> Payment:
    amount = money(amount)
    if amount <= Decimal("0.00"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment amount must be greater than zero")
    if amount > bill.balance:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment exceeds remaining bill balance")

    payment = Payment(
        bill_id=bill.id,
        landlord_id=bill.landlord_id,
        tenant_id=bill.tenant_id,
        amount=amount,
        paid_at=paid_at or datetime.now(timezone.utc),
        note=note,
    )
    db.add(payment)
    bill.amount_paid = money(bill.amount_paid + amount)
    refresh_bill_status(bill)
    db.flush()
    return payment
