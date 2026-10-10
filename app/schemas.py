from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=72)
    role: Literal["landlord", "tenant"]


class UserRead(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
    user_id: Optional[int] = None


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
    tenant_ids: list[int] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=255)
    amount_due: Decimal = Field(gt=0, decimal_places=2)
    recurrence: Literal["one_time", "monthly", "annually", "custom_period"]
    due_date: date
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    @model_validator(mode="after")
    def validate_period(self) -> "ManualBillCreate":
        if self.recurrence in {"monthly", "annually", "custom_period"}:
            if self.period_start is None or self.period_end is None:
                raise ValueError("period_start and period_end are required for recurring or custom-period bills")
            if self.period_end < self.period_start:
                raise ValueError("period_end must be on or after period_start")
        return self


class PropertyBillBase(BaseModel):
    property_id: int
    bill_type: Literal["electricity", "water", "mortgage", "insurance", "property_tax", "maintenance", "other"]
    title: str = Field(min_length=1, max_length=255)
    amount_due: Decimal = Field(gt=0, decimal_places=2)
    recurrence: Literal["one_time", "monthly", "annually", "custom_period"]
    due_date: date
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_period(self) -> "PropertyBillBase":
        if self.recurrence in {"monthly", "annually", "custom_period"}:
            if self.period_start is None or self.period_end is None:
                raise ValueError("period_start and period_end are required for recurring or custom-period bills")
            if self.period_end < self.period_start:
                raise ValueError("period_end must be on or after period_start")
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
