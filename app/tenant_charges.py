from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.billing import month_window, split_amount
from app.models import Lease, LeaseTenant, PropertyBill, RentalBill
from app.schemas import TenantChargePreview, TenantChargeShare


def preview_tenant_charges(db: Session, bill: PropertyBill) -> TenantChargePreview:
    existing = db.scalar(
        select(RentalBill.id).where(RentalBill.source_property_bill_id == bill.id).limit(1)
    )
    if bill.generated_at is not None or existing is not None:
        raise HTTPException(status_code=409, detail="Tenant charges have already been generated for this bill.")

    period_start = bill.period_start or bill.due_date
    period_end = bill.period_end or bill.due_date
    leases = db.scalars(
        select(Lease)
        .options(selectinload(Lease.tenants).selectinload(LeaseTenant.tenant))
        .where(
            Lease.landlord_id == bill.landlord_id,
            Lease.property_id == bill.property_id,
            Lease.start_date <= period_end,
            Lease.end_date >= period_start,
        )
        .order_by(Lease.start_date.desc(), Lease.id.desc())
    )
    # Count each tenant once even if they have multiple leases in the period.
    tenant_leases = {}
    for lease in leases:
        for terms in lease.tenants:
            if terms.tenant.landlord_id == bill.landlord_id:
                tenant_leases.setdefault(terms.tenant_id, (lease.id, terms.tenant.full_name))
    tenant_ids = sorted(tenant_leases)
    shares = split_amount(bill.amount_due, len(tenant_ids)) if tenant_ids else []
    preview = TenantChargePreview(
        property_bill_id=bill.id,
        property_name=bill.property.name,
        title=bill.title,
        bill_type=bill.bill_type,
        recurrence=bill.recurrence,
        amount_due=bill.amount_due,
        due_date=month_window(bill.due_date)[1],
        period_start=period_start,
        period_end=period_end,
        tenants=[
            TenantChargeShare(
                tenant_id=tenant_id,
                lease_id=tenant_leases[tenant_id][0],
                tenant_name=tenant_leases[tenant_id][1],
                amount_due=share,
            )
            for tenant_id, share in zip(tenant_ids, shares)
        ],
    )
    # Include the source date so same-month edits also require a fresh confirmation.
    token_data = preview.model_dump_json(exclude={"preview_token"}) + f"|{bill.due_date.isoformat()}"
    preview.preview_token = sha256(token_data.encode()).hexdigest()
    return preview
