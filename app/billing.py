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


def month_window(billing_date: date) -> tuple[date, date]:
    last_day = monthrange(billing_date.year, billing_date.month)[1]
    return date(billing_date.year, billing_date.month, 1), date(billing_date.year, billing_date.month, last_day)


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


def prorate_monthly_rent(monthly_rent: Decimal, active_start: date, active_end: date) -> Decimal:
    days_in_month = Decimal(monthrange(active_start.year, active_start.month)[1])
    active_days = Decimal((active_end - active_start).days + 1)
    return money(monthly_rent * active_days / days_in_month)


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


def generate_bills_for_lease(db: Session, lease: Lease, billing_date: Optional[date] = None) -> list[RentalBill]:
    billing_date = billing_date or date.today()
    period_start, period_end = month_window(billing_date)
    if lease.start_date > period_end or lease.end_date < period_start:
        return []

    lease_tenants = list(lease.tenants)
    if not lease_tenants:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lease must have at least one tenant")

    active_start = max(lease.start_date, period_start)
    active_end = min(lease.end_date, period_end)
    bills: list[RentalBill] = []

    for lease_tenant in lease_tenants:
        tenant_id = lease_tenant.tenant_id
        amount_due = prorate_monthly_rent(lease_tenant.monthly_rent, active_start, active_end)
        existing = db.scalar(
            select(RentalBill).where(
                RentalBill.lease_id == lease.id,
                RentalBill.tenant_id == tenant_id,
                RentalBill.bill_year == billing_date.year,
                RentalBill.bill_month == billing_date.month,
                RentalBill.bill_type == "rent",
            )
        )
        if existing:
            bills.append(existing)
            continue

        bill = RentalBill(
            landlord_id=lease.landlord_id,
            lease_id=lease.id,
            tenant_id=tenant_id,
            title="Monthly rent",
            bill_type="rent",
            recurrence="monthly",
            period_start=active_start,
            period_end=active_end,
            bill_year=billing_date.year,
            bill_month=billing_date.month,
            due_date=active_start,
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
