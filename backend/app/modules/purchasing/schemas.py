"""Request and response shapes for purchasing."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import MoneyMixin
from app.modules.purchasing.enums import (
    BillKind,
    BillStatus,
    OrderStatus,
    RequestStatus,
    SupplierPaymentMethod,
    SupplierPaymentStatus,
)


def _reject_float(value: object) -> object:
    """Quantities and prices are decimals. `0.1 + 0.2` is not `0.3`."""
    if isinstance(value, float):
        raise ValueError("Pass amounts as a string or a decimal, not a binary float")
    return value


# ------------------------------------------------------------------ suppliers


class SupplierCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tax_number: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = None
    notes: str | None = None


class SupplierUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    tax_number: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    tax_number: str | None
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool


class SupplierBalanceResponse(BaseModel, MoneyMixin):
    supplier_id: uuid.UUID
    billed: Decimal
    paid: Decimal
    outstanding: Decimal
    advances_available: Decimal
    currency: str


# ------------------------------------------------------------------- requests


class RequestLineInputModel(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0)
    unit: str | None = Field(default=None, max_length=16)
    estimated_unit_price: Decimal | None = Field(default=None, ge=0)

    @field_validator("quantity", "estimated_unit_price", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class RequestCreateRequest(BaseModel):
    lines: list[RequestLineInputModel] = Field(min_length=1)
    reason: str | None = None
    needed_by: date | None = None


class RequestStatusRequest(BaseModel):
    status: RequestStatus
    note: str | None = None


class RequestLineResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    description: str
    quantity: Decimal
    unit: str | None
    estimated_unit_price: Decimal | None


class PurchaseRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    status: RequestStatus
    requested_by_user_id: uuid.UUID | None
    needed_by: date | None
    reason: str | None
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    self_approved: bool


class PurchaseRequestDetail(PurchaseRequestResponse):
    lines: list[RequestLineResponse]


# --------------------------------------------------------------------- orders


class OrderLineInputModel(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    unit: str | None = Field(default=None, max_length=16)
    #: The stocked material being ordered, when it is one.
    item_id: uuid.UUID | None = None

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class OrderCreateRequest(BaseModel):
    supplier_id: uuid.UUID
    lines: list[OrderLineInputModel] = Field(min_length=1)
    ordered_on: date | None = None
    expected_on: date | None = None
    request_id: uuid.UUID | None = None
    currency: str = Field(default="UZS", min_length=3, max_length=3)
    notes: str | None = None


class OrderStatusRequest(BaseModel):
    status: OrderStatus
    reason: str | None = None


class OrderLineResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    item_id: uuid.UUID | None
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    line_total: Decimal


class PurchaseOrderResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    code: str
    supplier_id: uuid.UUID
    supplier_name: str
    request_id: uuid.UUID | None
    status: OrderStatus
    ordered_on: date
    expected_on: date | None
    currency: str
    total: Decimal
    notes: str | None
    raised_by_user_id: uuid.UUID | None
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    self_approved: bool
    closed_reason: str | None


class OutstandingLineResponse(BaseModel, MoneyMixin):
    order_line_id: uuid.UUID
    description: str
    unit: str | None
    ordered: Decimal
    received: Decimal
    outstanding: Decimal


class PurchaseOrderDetail(PurchaseOrderResponse):
    lines: list[OrderLineResponse]
    #: What is still to come. Derived from the receipts every time it is
    #: asked for, never stored as a running total on the line.
    outstanding: list[OutstandingLineResponse]


# ------------------------------------------------------------------- receipts


class ReceiptLineInputModel(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0)
    order_line_id: uuid.UUID | None = None
    unit: str | None = Field(default=None, max_length=16)
    rejected_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    rejected_reason: str | None = None
    #: Naming a stocked material is what puts this line on a shelf: the
    #: accepted quantity becomes a stock movement in the same transaction.
    item_id: uuid.UUID | None = None
    #: Which shelf. The organization's default is used when it is not said.
    location_id: uuid.UUID | None = None
    #: The lot number on the box, required for a material tracked by batch.
    batch_code: str | None = Field(default=None, max_length=64)
    batch_expires_on: date | None = None

    @field_validator("quantity", "rejected_quantity", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class ReceiptCreateRequest(BaseModel):
    supplier_id: uuid.UUID
    lines: list[ReceiptLineInputModel] = Field(min_length=1)
    order_id: uuid.UUID | None = None
    received_on: date | None = None
    delivery_note: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class ReceiptLineResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    receipt_id: uuid.UUID
    order_line_id: uuid.UUID | None
    item_id: uuid.UUID | None
    description: str
    quantity: Decimal
    unit: str | None
    rejected_quantity: Decimal
    rejected_reason: str | None


class GoodsReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    supplier_id: uuid.UUID
    order_id: uuid.UUID | None
    received_on: date
    received_by_user_id: uuid.UUID | None
    delivery_note: str | None
    notes: str | None


class GoodsReceiptDetail(GoodsReceiptResponse):
    lines: list[ReceiptLineResponse]


class UninvoicedLineResponse(BaseModel, MoneyMixin):
    """A delivery line nobody has billed for yet."""

    id: uuid.UUID
    receipt_id: uuid.UUID
    receipt_code: str
    received_on: date
    description: str
    quantity: Decimal
    unit: str | None


# ---------------------------------------------------------------------- bills


class BillLineInputModel(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    unit: str | None = Field(default=None, max_length=16)
    #: The delivery line this charge is for, when there is one. A delivery
    #: line may be billed once — which is what stops a supplier invoicing
    #: the same box twice.
    receipt_line_id: uuid.UUID | None = None

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class BillCreateRequest(BaseModel):
    supplier_id: uuid.UUID
    kind: BillKind
    lines: list[BillLineInputModel] = Field(min_length=1)
    invoice_date: date | None = None
    due_date: date | None = None
    order_id: uuid.UUID | None = None
    supplier_reference: str | None = Field(default=None, max_length=64)
    currency: str = Field(default="UZS", min_length=3, max_length=3)
    notes: str | None = None


class VoidBillRequest(BaseModel):
    reason: str = Field(min_length=3)


class BillLineResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    line_total: Decimal
    receipt_line_id: uuid.UUID | None


class SupplierInvoiceResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    code: str
    supplier_id: uuid.UUID
    supplier_name: str
    order_id: uuid.UUID | None
    supplier_reference: str | None
    kind: BillKind
    status: BillStatus
    invoice_date: date
    due_date: date | None
    currency: str
    total: Decimal
    #: Derived from live allocations on every read, never stored.
    paid: Decimal
    remaining: Decimal
    registered_at: datetime | None
    void_reason: str | None
    notes: str | None


class SupplierInvoiceDetail(SupplierInvoiceResponse):
    lines: list[BillLineResponse]


# ------------------------------------------------------------------- payments


class PaymentCreateRequest(BaseModel, MoneyMixin):
    supplier_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    method: SupplierPaymentMethod
    paid_on: date | None = None
    #: With a bill, the payment settles it. Without one, it is an advance.
    invoice_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=64)
    notes: str | None = None
    currency: str = Field(default="UZS", min_length=3, max_length=3)
    #: Required when the method is CASH: money leaving the till has to move
    #: the drawer too, or the count at the end of the day will not agree.
    cash_session_id: uuid.UUID | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class ApplyAdvanceRequest(BaseModel, MoneyMixin):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0)

    @field_validator("amount", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class ReverseAllocationRequest(BaseModel):
    reason: str = Field(min_length=3)


class SupplierPaymentResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    code: str
    supplier_id: uuid.UUID
    supplier_name: str
    amount: Decimal
    currency: str
    method: SupplierPaymentMethod
    status: SupplierPaymentStatus
    paid_on: date
    reference: str | None
    notes: str | None
    #: The part not applied to any bill — an advance held with the supplier.
    unapplied: Decimal


class AllocationResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    payment_id: uuid.UUID
    invoice_id: uuid.UUID
    amount: Decimal
    reversed_at: datetime | None
    reversed_reason: str | None


class PayablesAgeingRowResponse(BaseModel, MoneyMixin):
    supplier_id: uuid.UUID
    supplier_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    days_over_90: Decimal
    total: Decimal
