"""Cash request and response shapes. Money travels as decimal strings."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import MoneyMixin
from app.modules.cash.enums import DepositRoute, MovementKind, SessionStatus


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("Send monetary amounts as strings, not JSON numbers")
    return value


class CashRegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    account_id: uuid.UUID
    currency: str
    is_active: bool


class CashRegisterCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    #: The ledger account the till's balance belongs to — normally 5010.
    account_code: str = Field(default="5010", max_length=32)
    currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")


class SessionTotalsResponse(BaseModel, MoneyMixin):
    opening_float: Decimal
    receipts: Decimal
    refunds: Decimal
    expenses: Decimal
    deposits: Decimal
    adjustments_in: Decimal
    adjustments_out: Decimal
    #: Derived from the movements. There is no stored field to edit.
    expected: Decimal
    counted: Decimal | None
    discrepancy: Decimal | None


class CashSessionResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    register_id: uuid.UUID
    business_date: date
    status: SessionStatus
    currency: str
    opened_at: datetime
    counted_amount: Decimal | None
    counted_at: datetime | None
    discrepancy: Decimal | None
    discrepancy_reason: str | None
    approved_at: datetime | None
    closed_at: datetime | None


class CashMovementResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: MovementKind
    amount: Decimal
    description: str | None
    occurred_at: datetime
    payment_id: uuid.UUID | None
    deposit_route: DepositRoute | None
    reference: str | None


class CashSessionDetail(CashSessionResponse):
    register_code: str
    register_name: str
    totals: SessionTotalsResponse
    movements: list[CashMovementResponse]


class OpenSessionRequest(BaseModel, MoneyMixin):
    register_id: uuid.UUID
    business_date: date
    opening_float: Decimal = Decimal("0")

    @field_validator("opening_float", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class CashRefundRequest(BaseModel):
    """Refund a cash payment out of the open drawer.

    No amount: a refund is of the whole payment, and the figure comes from
    the payment record rather than from whoever is asking.
    """

    payment_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=1000)


class CashExpenseRequest(BaseModel, MoneyMixin):
    amount: Decimal
    description: str = Field(min_length=1, max_length=1000)

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class CashDepositRequest(BaseModel, MoneyMixin):
    amount: Decimal
    route: DepositRoute
    reference: str | None = Field(default=None, max_length=128)

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class SubmitCountRequest(BaseModel, MoneyMixin):
    counted_amount: Decimal
    #: Required when the count differs from the expected balance.
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("counted_amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class CashPaymentRequest(BaseModel, MoneyMixin):
    """Take a cash payment and record it in the open session, in one step."""

    customer_id: uuid.UUID
    amount: Decimal
    received_on: date
    allocations: list[dict] = Field(default_factory=list)
    auto_allocate: bool = True
    reference: str | None = Field(default=None, max_length=128)
    notes: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)
