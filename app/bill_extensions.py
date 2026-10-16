"""Preview and extend property bill series without modifying existing expenses."""

from datetime import date, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing import money, monthly_due_date
from app.models import PropertyBill, PropertyBillSeries, PropertyBillSeriesMember
from app.schemas import (
    ConfirmPropertyBillExtension, ExtendPropertyBillRequest, PropertyBillExtensionPreview,
    PropertyBillRead, ProposedPropertyBill,
)


MONTH_STEPS = {"monthly": 1, "bi_monthly": 2, "quarterly": 3, "semi_annually": 6, "annually": 12}


def track_created_series(db: Session, payload, bills: list[PropertyBill], landlord_id: int) -> None:
    if payload.recurrence not in MONTH_STEPS:
        return
    series = PropertyBillSeries(
        id=str(uuid4()), landlord_id=landlord_id,
        anchor=payload.due_date if payload.recurrence == "annually" else payload.period_start,
        due_day=payload.due_date.day, recurrence=payload.recurrence, through=payload.period_end,
    )
    db.add(series)
    db.flush()
    for bill in bills:
        db.add(PropertyBillSeriesMember(
            bill_id=bill.id, series_id=series.id, scheduled_date=bill.due_date,
        ))


def series_context(db: Session, source: PropertyBill):
    if source.recurrence not in MONTH_STEPS or not source.period_start or not source.period_end:
        raise HTTPException(400, "Only repeated property bills with a valid period can be extended.")
    member = db.get(PropertyBillSeriesMember, source.id)
    if member:
        series = db.scalar(select(PropertyBillSeries).where(
            PropertyBillSeries.id == member.series_id, PropertyBillSeries.landlord_id == source.landlord_id,
        ))
        if series is None:
            raise HTTPException(409, "The bill series is unavailable.")
        bills = list(db.scalars(
            select(PropertyBill).join(PropertyBillSeriesMember, PropertyBillSeriesMember.bill_id == PropertyBill.id)
            .where(PropertyBillSeriesMember.series_id == series.id, PropertyBill.landlord_id == source.landlord_id)
            .order_by(PropertyBill.due_date, PropertyBill.id)
        ))
        return series, bills, None

    # Older records have no batch ID. Match only the original batch signature,
    # including creation time; never silently combine overlapping copies.
    bills = list(db.scalars(select(PropertyBill).where(
        PropertyBill.landlord_id == source.landlord_id,
        PropertyBill.property_id == source.property_id,
        PropertyBill.bill_type == source.bill_type,
        PropertyBill.recurrence == source.recurrence,
        PropertyBill.period_start == source.period_start,
        PropertyBill.period_end == source.period_end,
    ).order_by(PropertyBill.due_date, PropertyBill.id)))
    # SQLite's server timestamps omit fractional seconds, unlike bound datetimes.
    bills = [bill for bill in bills if bill.created_at == source.created_at and bill.title == source.title]
    slots = {(bill.due_date.year, bill.due_date.month) for bill in bills}
    claimed = db.scalar(select(PropertyBillSeriesMember.bill_id).where(
        PropertyBillSeriesMember.bill_id.in_([bill.id for bill in bills]),
    ).limit(1))
    if len(slots) != len(bills) or claimed is not None:
        raise HTTPException(409, "These older bills have ambiguous series membership. Resolve duplicate bills before extending.")
    series = PropertyBillSeries(
        landlord_id=source.landlord_id,
        anchor=min(bill.due_date for bill in bills) if source.recurrence == "annually" else source.period_start,
        due_day=max(bill.due_date.day for bill in bills),
        recurrence=source.recurrence, through=source.period_end, version=0,
    )
    warning = (
        "Older bills were matched by property, title, category, recurrence, original period, and creation time. "
        "Review the existing bills and due day before confirming; the original preferred day may have been clamped."
    )
    return series, bills, warning


def preview_extension(db: Session, source: PropertyBill, payload: ExtendPropertyBillRequest):
    series, bills, warning = series_context(db, source)
    if payload.through <= series.through:
        raise HTTPException(400, f"Choose an end date after {series.through.isoformat()}.")
    if (payload.through - series.through).days > 3660:
        raise HTTPException(400, "Extend at most ten years at a time.")
    due_day = payload.due_day or series.due_day
    step = MONTH_STEPS[series.recurrence]
    anchor_month = series.anchor.year * 12 + series.anchor.month - 1
    current_month = series.through.year * 12 + series.through.month - 1
    # Retain the original cadence, including a not-yet-covered due day in the
    # current month when a previous extension ended mid-month.
    offset = max(0, (current_month - anchor_month + step - 1) // step) * step
    last_month = payload.through.year * 12 + payload.through.month - 1
    dates = []
    while anchor_month + offset <= last_month:
        year, month = divmod(anchor_month + offset, 12)
        due = monthly_due_date(year, month + 1, due_day)
        if series.through < due <= payload.through:
            dates.append(due)
        offset += step
    # A manually moved existing bill occupies its displayed month as well.
    occupied = {(bill.due_date.year, bill.due_date.month) for bill in bills}
    if series.id is not None:
        occupied.update(
            (scheduled.year, scheduled.month)
            for scheduled in db.scalars(select(PropertyBillSeriesMember.scheduled_date).where(
                PropertyBillSeriesMember.series_id == series.id,
            ))
        )
    dates = [due for due in dates if (due.year, due.month) not in occupied]
    preview = PropertyBillExtensionPreview(
        source_bill_id=source.id, title=source.title, property_name=source.property.name,
        recurrence=series.recurrence, current_end=series.through, through=payload.through,
        due_day=due_day,
        existing_future_bills=[PropertyBillRead.model_validate(bill) for bill in bills if bill.due_date >= date.today()],
        new_bills=[ProposedPropertyBill(due_date=due, amount_due=source.amount_due) for due in dates],
        total_new_amount=money(source.amount_due * len(dates)), warning=warning,
    )
    # Include all members (also past bills), source edits and series version.
    fingerprint = (
        preview.model_dump_json(exclude={"preview_token"})
        + "|".join(PropertyBillRead.model_validate(bill).model_dump_json() for bill in bills)
        + f"|{series.id}|{series.version}|{series.anchor}|{source.id}"
    )
    preview.preview_token = sha256(fingerprint.encode()).hexdigest()
    return preview, series, bills


def confirm_extension(db: Session, source: PropertyBill, payload: ConfirmPropertyBillExtension) -> list[PropertyBill]:
    preview, series, existing = preview_extension(db, source, payload)
    if payload.preview_token != preview.preview_token:
        raise HTTPException(409, "Bills changed since preview. Refresh the preview and confirm again.")
    if not preview.new_bills:
        raise HTTPException(400, "There are no new bills to generate for this end date.")
    try:
        if series.id is None:
            series.id = str(uuid4())
            db.add(series)
            db.flush()
            for bill in existing:
                db.add(PropertyBillSeriesMember(
                    bill_id=bill.id, series_id=series.id, scheduled_date=bill.due_date,
                ))
            db.flush()
        previous_end = series.through
        claimed = db.execute(
            update(PropertyBillSeries).where(
                PropertyBillSeries.id == series.id,
                PropertyBillSeries.landlord_id == source.landlord_id,
                PropertyBillSeries.version == series.version,
            ).values(through=payload.through, due_day=preview.due_day, version=series.version + 1)
        )
        if claimed.rowcount != 1:
            raise HTTPException(409, "Another extension was confirmed. Refresh the preview.")
        created = []
        for proposed in preview.new_bills:
            bill = PropertyBill(
                landlord_id=source.landlord_id, property_id=source.property_id,
                title=source.title, bill_type=source.bill_type, recurrence=source.recurrence,
                amount_due=proposed.amount_due, due_date=proposed.due_date,
                period_start=previous_end + timedelta(days=1), period_end=payload.through,
                status="unpaid", notes=source.notes,
            )
            db.add(bill)
            db.flush()
            db.add(PropertyBillSeriesMember(
                bill_id=bill.id, series_id=series.id, scheduled_date=bill.due_date,
            ))
            created.append(bill)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This series changed or was already extended. Refresh the preview.") from exc
    except Exception:
        db.rollback()
        raise
    for bill in created:
        db.refresh(bill)
    return created
