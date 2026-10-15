from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# Bounds the rows one request can create; matches the ten-year limit on extending a bill series.
MAX_BILL_PERIOD_YEARS = 10
MAX_BILL_TENANTS = 50


def ensure_bill_period_span(period_start: Optional[date], period_end: Optional[date]) -> None:
    if period_start is None or period_end is None:
        return
    limit_year = period_start.year + MAX_BILL_PERIOD_YEARS
    if limit_year > date.max.year:
        return
    try:
        limit = period_start.replace(year=limit_year)
    except ValueError:  # February 29
        limit = period_start.replace(year=limit_year, day=28)
    if period_end > limit:
        raise ValueError(f"The bill period can span at most {MAX_BILL_PERIOD_YEARS} years")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=72)
    role: Literal["landlord", "tenant"]


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str
    display_name: Optional[str] = None
    role: str
    password_change_required: bool = False
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: EmailStr | Literal["admin"]
    password: str


class LandlordAdminRead(BaseModel):
    user_id: int
    landlord_id: Optional[int]
    full_name: str
    email: str
    is_active: bool
    last_login_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    active_sessions: int
    login_status: Literal["signed_in", "signed_out", "never_logged_in"]
    created_at: datetime


class LandlordStatusUpdate(BaseModel):
    is_active: bool


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)

    @model_validator(mode="after")
    def validate_password_bytes(self) -> "AdminPasswordReset":
        if len(self.new_password.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return self


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=255)
    current_password: Optional[str] = None
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_password_bytes(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class PropertyBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1, max_length=500)
    description: Optional[str] = None


class PropertyCreate(PropertyBase):
    pass


class PropertyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    address: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = None


class PropertyRead(PropertyBase):
    id: int
    landlord_id: int

    model_config = ConfigDict(from_attributes=True)


class UnitBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class UnitCreate(UnitBase):
    pass


class UnitUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None


class UnitRead(UnitBase):
    id: int
    property_id: int

    model_config = ConfigDict(from_attributes=True)


class TenantBase(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = None


class TenantCreate(TenantBase):
    # Linking to an existing user account by ID is not accepted: it would let any landlord
    # claim another person's tenant account and read its email.
    model_config = ConfigDict(extra="forbid")


class TenantUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = None


class TenantRead(TenantBase):
    id: int
    user_id: Optional[int]
    landlord_id: Optional[int]

    model_config = ConfigDict(from_attributes=True)


class LeaseTenantTerms(BaseModel):
    tenant_id: int
    monthly_rent: Decimal = Field(gt=0, decimal_places=2)
    deposit: Decimal = Field(ge=0, decimal_places=2)


class LeaseTenantTermsRead(BaseModel):
    tenant_id: int
    monthly_rent: Decimal = Field(ge=0, decimal_places=2)
    deposit: Decimal = Field(ge=0, decimal_places=2)


class LeaseBase(BaseModel):
    property_id: int
    unit_id: Optional[int] = None
    start_date: date
    end_date: date
    monthly_rent: Decimal = Field(gt=0, decimal_places=2)
    tenants: list[LeaseTenantTerms] = Field(min_length=1)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "LeaseBase":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        tenant_ids = [tenant.tenant_id for tenant in self.tenants]
        if len(tenant_ids) != len(set(tenant_ids)):
            raise ValueError("tenant ids must be unique")
        rent_total = sum((tenant.monthly_rent for tenant in self.tenants), Decimal("0.00"))
        if rent_total != self.monthly_rent:
            raise ValueError("tenant monthly rents must add up to lease monthly_rent")
        return self


class LeaseCreate(LeaseBase):
    pass


class LeaseUpdate(BaseModel):
    property_id: Optional[int] = None
    unit_id: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    monthly_rent: Optional[Decimal] = Field(default=None, gt=0, decimal_places=2)
    tenants: Optional[list[LeaseTenantTerms]] = Field(default=None, min_length=1)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "LeaseUpdate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.tenants is not None:
            tenant_ids = [tenant.tenant_id for tenant in self.tenants]
            if len(tenant_ids) != len(set(tenant_ids)):
                raise ValueError("tenant ids must be unique")
            if self.monthly_rent is not None:
                rent_total = sum((tenant.monthly_rent for tenant in self.tenants), Decimal("0.00"))
                if rent_total != self.monthly_rent:
                    raise ValueError("tenant monthly rents must add up to lease monthly_rent")
        return self


class LeaseRead(BaseModel):
    id: int
    landlord_id: int
    property_id: int
    unit_id: Optional[int]
    start_date: date
    end_date: date
    monthly_rent: Decimal
    tenant_ids: list[int]
    tenants: list[LeaseTenantTermsRead]
    notes: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class BillRead(BaseModel):
    id: int
    landlord_id: int
    lease_id: int
    tenant_id: int
    tenant_name: str
    is_tenant_charge: bool
    source_property_bill_id: Optional[int]
    title: str
    bill_type: str
    recurrence: Optional[str]
    period_start: Optional[date]
    period_end: Optional[date]
    bill_year: int
    bill_month: int
    due_date: date
    amount_due: Decimal
    amount_paid: Decimal
    balance: Decimal
    status: str

    model_config = ConfigDict(from_attributes=True)


class ManualBillCreate(BaseModel):
    lease_id: int
    tenant_ids: list[int] = Field(min_length=1, max_length=MAX_BILL_TENANTS)
    title: str = Field(min_length=1, max_length=255)
    amount_due: Decimal = Field(gt=0, decimal_places=2)
    recurrence: Literal["one_time", "monthly", "bi_monthly", "quarterly", "semi_annually", "annually", "custom_period"]
    due_date: date
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    @model_validator(mode="after")
    def validate_period(self) -> "ManualBillCreate":
        if self.recurrence in {"monthly", "bi_monthly", "quarterly", "semi_annually", "annually", "custom_period"}:
            if self.period_start is None or self.period_end is None:
                raise ValueError("period_start and period_end are required for recurring or custom-period bills")
            if self.period_end < self.period_start:
                raise ValueError("period_end must be on or after period_start")
        ensure_bill_period_span(self.period_start, self.period_end)
        return self


class PropertyBillBase(BaseModel):
    property_id: int
    bill_type: Literal["electricity", "water", "mortgage", "insurance", "property_tax", "maintenance", "other"]
    title: str = Field(min_length=1, max_length=255)
    amount_due: Decimal = Field(gt=0, decimal_places=2)
    recurrence: Literal["one_time", "monthly", "bi_monthly", "quarterly", "semi_annually", "annually", "custom_period"]
    due_date: date
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_period(self) -> "PropertyBillBase":
        if self.recurrence in {"monthly", "bi_monthly", "quarterly", "semi_annually", "annually", "custom_period"}:
            if self.period_start is None or self.period_end is None:
                raise ValueError("period_start and period_end are required for recurring or custom-period bills")
            if self.period_end < self.period_start:
                raise ValueError("period_end must be on or after period_start")
        ensure_bill_period_span(self.period_start, self.period_end)
        return self


class PropertyBillCreate(PropertyBillBase):
    pass


class PropertyBillUpdate(BaseModel):
    bill_type: Optional[Literal["electricity", "water", "mortgage", "insurance", "property_tax", "maintenance", "other"]] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    amount_due: Optional[Decimal] = Field(default=None, gt=0, decimal_places=2)
    due_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    status: Optional[Literal["unpaid", "paid"]] = None
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_period(self) -> "PropertyBillUpdate":
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        ensure_bill_period_span(self.period_start, self.period_end)
        return self


class PropertyBillRead(BaseModel):
    id: int
    landlord_id: int
    property_id: int
    bill_type: str
    title: str
    amount_due: Decimal
    recurrence: str
    due_date: date
    period_start: Optional[date]
    period_end: Optional[date]
    status: str
    paid_at: Optional[datetime]
    generated_at: Optional[datetime]
    notes: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class ExtendPropertyBillRequest(BaseModel):
    through: date
    due_day: Optional[int] = Field(default=None, ge=1, le=31)


class ConfirmPropertyBillExtension(ExtendPropertyBillRequest):
    preview_token: str = Field(min_length=64, max_length=64)


class ProposedPropertyBill(BaseModel):
    due_date: date
    amount_due: Decimal


class PropertyBillExtensionPreview(BaseModel):
    source_bill_id: int
    title: str
    property_name: str
    recurrence: str
    current_end: date
    through: date
    due_day: int
    existing_future_bills: list[PropertyBillRead]
    new_bills: list[ProposedPropertyBill]
    total_new_amount: Decimal
    warning: Optional[str] = None
    preview_token: str = ""


class TenantChargeShare(BaseModel):
    tenant_id: int
    lease_id: int
    tenant_name: str
    amount_due: Decimal


class TenantChargePreview(BaseModel):
    property_bill_id: int
    property_name: str
    title: str
    bill_type: str
    recurrence: str
    amount_due: Decimal
    due_date: date
    period_start: date
    period_end: date
    tenants: list[TenantChargeShare]
    preview_token: str = ""


class GenerateTenantCharges(BaseModel):
    preview_token: str = Field(min_length=64, max_length=64)


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    paid_at: Optional[datetime] = None
    note: Optional[str] = None


class PaymentRead(BaseModel):
    id: int
    bill_id: int
    landlord_id: int
    tenant_id: int
    amount: Decimal
    paid_at: datetime
    note: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class PaymentResult(BaseModel):
    payment: PaymentRead
    bill: BillRead
