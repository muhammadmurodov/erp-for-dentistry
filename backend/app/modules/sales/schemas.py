"""Sales request and response shapes. Money travels as decimal strings."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import MoneyMixin
from app.modules.sales.enums import (
    CustomerKind,
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    RevenueTaxTreatment,
    ServiceCategory,
)


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("Send monetary amounts as strings, not JSON numbers")
    return value


# ------------------------------------------------------------- customers


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str
    kind: CustomerKind
    tax_number: str | None
    phone: str | None
    email: str | None
    is_active: bool


class CustomerBalanceResponse(BaseModel, MoneyMixin):
    customer_id: uuid.UUID
    invoiced: Decimal
    paid: Decimal
    outstanding: Decimal
    #: Money received with nothing yet to apply it to — a liability.
    unapplied_credit: Decimal


class CustomerCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=255)
    kind: CustomerKind = CustomerKind.PERSON
    tax_number: str | None = Field(default=None, pattern=r"^[0-9]{9,14}$")
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    notes: str | None = None


# -------------------------------------------------------------- services


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    name_uz: str | None
    name_ru: str | None
    category: ServiceCategory
    mxik_code: str | None
    unit_label: str | None
    is_active: bool


class ServiceWithPrice(ServiceResponse, MoneyMixin):
    current_price: Decimal | None
    price_effective_from: date | None


class ServiceCreateRequest(BaseModel, MoneyMixin):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    category: ServiceCategory = ServiceCategory.TREATMENT
    name_uz: str | None = Field(default=None, max_length=255)
    name_ru: str | None = Field(default=None, max_length=255)
    #: ИКПУ / MXIK — statutory classification required on a fiscal receipt.
    mxik_code: str | None = Field(default=None, max_length=32)
    unit_code: str | None = Field(default=None, max_length=16)
    unit_label: str | None = Field(default=None, max_length=32)
    description: str | None = None
    price: Decimal | None = None
    effective_from: date | None = None

    @field_validator("price", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class ServicePriceRequest(BaseModel, MoneyMixin):
    amount: Decimal
    effective_from: date
    currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class ServicePriceResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: Decimal
    currency: str
    effective_from: date
    effective_to: date | None


# -------------------------------------------------------------- invoices


class InvoiceLineInputSchema(BaseModel, MoneyMixin):
    description: str = Field(min_length=1)
    quantity: Decimal = Decimal("1")
    service_id: uuid.UUID | None = None
    #: Omit to take the price in force on the invoice date.
    unit_price: Decimal | None = None

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class InvoiceLineResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_number: int
    service_id: uuid.UUID | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    mxik_code: str | None


class InvoiceResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    status: InvoiceStatus
    issue_date: date
    due_date: date | None
    currency: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    tax_treatment: str
    notes: str | None
    issued_at: datetime | None
    voided_at: datetime | None
    void_reason: str | None
    #: Computed from the allocation rows, never stored.
    allocated: Decimal = Decimal("0")
    outstanding: Decimal = Decimal("0")


class InvoiceDetail(InvoiceResponse):
    lines: list[InvoiceLineResponse]
    customer_name: str


class InvoiceCreateRequest(BaseModel):
    customer_id: uuid.UUID
    issue_date: date
    lines: list[InvoiceLineInputSchema] = Field(min_length=1, max_length=200)
    due_date: date | None = None
    currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")
    notes: str | None = None


class IssueInvoiceRequest(BaseModel):
    #: Stated explicitly rather than defaulted — see OPEN_QUESTIONS A5.
    tax_treatment: RevenueTaxTreatment


class VoidInvoiceRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


# -------------------------------------------------------------- payments


class AllocationRequest(BaseModel, MoneyMixin):
    invoice_id: uuid.UUID
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class PaymentCreateRequest(BaseModel, MoneyMixin):
    customer_id: uuid.UUID
    amount: Decimal
    method: PaymentMethod
    received_on: date
    currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")
    reference: str | None = Field(default=None, max_length=128)
    notes: str | None = None
    allocations: list[AllocationRequest] = Field(default_factory=list)
    #: Spread whatever is left over the customer's open invoices, oldest
    #: first. Anything still unallocated stays an advance.
    auto_allocate: bool = False

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class AllocationResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
    invoice_id: uuid.UUID
    amount: Decimal


class PaymentResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    received_on: date
    method: PaymentMethod
    status: PaymentStatus
    amount: Decimal
    currency: str
    reference: str | None
    notes: str | None
    refunded_at: datetime | None
    refund_reason: str | None
    allocated: Decimal = Decimal("0")
    #: Unallocated money is an advance, not revenue.
    advance: Decimal = Decimal("0")


class PaymentDetail(PaymentResponse):
    allocations: list[AllocationResponse]
    customer_name: str


class RefundRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class RemoveAllocationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


# ------------------------------------------------------------- receivables


class AgeingBucket(BaseModel, MoneyMixin):
    label: str
    from_days: int
    to_days: int | None
    amount: Decimal


class CustomerAgeing(BaseModel, MoneyMixin):
    customer_id: uuid.UUID
    customer_code: str
    customer_name: str
    total_outstanding: Decimal
    buckets: list[AgeingBucket]


class ReceivablesAgeing(BaseModel, MoneyMixin):
    as_of: date
    currency: str
    customers: list[CustomerAgeing]
    total_outstanding: Decimal
    bucket_totals: list[AgeingBucket]
