"""Request and response shapes for materials and stock.

Quantities are decimals and never floats. A float reaches a stock count as
2.9999999999999996 and somebody writes off the difference.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.inventory.enums import (
    AdjustmentStatus,
    InventoryValuationMethod,
    StockMovementKind,
    StockMovementSource,
)


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("Pass a quantity as a string or a decimal, not a binary float")
    return value


# ---------------------------------------------------------------- categories


class CategoryCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_active: bool


# ----------------------------------------------------------------- locations


class LocationCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    is_default: bool = False


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_default: bool
    is_active: bool


# --------------------------------------------------------------------- items


class ItemCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1, max_length=16)
    category_id: uuid.UUID | None = None
    reorder_level: Decimal | None = None
    is_batch_tracked: bool = False
    notes: str | None = None

    _no_floats = field_validator("reorder_level", mode="before")(_reject_float)


class ItemUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    category_id: uuid.UUID | None = None
    reorder_level: Decimal | None = None
    is_batch_tracked: bool | None = None
    notes: str | None = None
    is_active: bool | None = None

    _no_floats = field_validator("reorder_level", mode="before")(_reject_float)


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    unit: str
    category_id: uuid.UUID | None
    reorder_level: Decimal | None
    is_batch_tracked: bool
    notes: str | None
    is_active: bool


# ------------------------------------------------------------------- batches


class BatchCreateRequest(BaseModel):
    item_id: uuid.UUID
    code: str = Field(min_length=1, max_length=64)
    expires_on: date | None = None
    notes: str | None = None


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    code: str
    expires_on: date | None
    notes: str | None


# ------------------------------------------------------------------- balances


class BalanceRow(BaseModel):
    item_id: uuid.UUID
    item_code: str
    item_name: str
    unit: str
    location_id: uuid.UUID
    batch_id: uuid.UUID | None
    batch_code: str | None
    expires_on: date | None
    quantity: Decimal


class LowStockResponse(BaseModel):
    item_id: uuid.UUID
    code: str
    name: str
    unit: str
    on_hand: Decimal
    reorder_level: Decimal


class ExpiringResponse(BaseModel):
    item_id: uuid.UUID
    item_name: str
    unit: str
    batch_id: uuid.UUID
    batch_code: str
    expires_on: date
    quantity: Decimal
    days_remaining: int


# ------------------------------------------------------------------ movements


class ConsumptionRequest(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal
    location_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    treatment_item_id: uuid.UUID | None = None
    occurred_on: date | None = None

    _no_floats = field_validator("quantity", mode="before")(_reject_float)


class WriteOffRequest(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal
    reason: str = Field(min_length=1)
    location_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    occurred_on: date | None = None

    _no_floats = field_validator("quantity", mode="before")(_reject_float)


class TransferRequest(BaseModel):
    item_id: uuid.UUID
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    quantity: Decimal
    batch_id: uuid.UUID | None = None
    occurred_on: date | None = None

    _no_floats = field_validator("quantity", mode="before")(_reject_float)


class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    location_id: uuid.UUID
    batch_id: uuid.UUID | None
    kind: StockMovementKind
    quantity: Decimal
    occurred_on: date
    source_kind: StockMovementSource
    source_id: uuid.UUID | None
    reason: str | None


# ---------------------------------------------------------------- adjustments


class AdjustmentLineInput(BaseModel):
    item_id: uuid.UUID
    counted_quantity: Decimal
    batch_id: uuid.UUID | None = None
    note: str | None = None

    _no_floats = field_validator("counted_quantity", mode="before")(_reject_float)


class AdjustmentCreateRequest(BaseModel):
    location_id: uuid.UUID
    reason: str = Field(min_length=1)
    counted_on: date | None = None
    lines: list[AdjustmentLineInput] = Field(min_length=1)


class AdjustmentDecisionRequest(BaseModel):
    note: str | None = None


class AdjustmentLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    batch_id: uuid.UUID | None
    system_quantity: Decimal
    counted_quantity: Decimal
    note: str | None


class AdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    location_id: uuid.UUID
    counted_on: date
    status: AdjustmentStatus
    reason: str
    counted_by_user_id: uuid.UUID | None
    decided_by_user_id: uuid.UUID | None
    decision_note: str | None


class AdjustmentDetailResponse(AdjustmentResponse):
    lines: list[AdjustmentLineResponse]


# ------------------------------------------------------------------ valuation


class ValuationRow(BaseModel):
    """One item's quantity, and what it is worth if that can be said at all."""

    item_id: uuid.UUID
    code: str
    name: str
    unit: str
    quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal | None


class ValuationResponse(BaseModel):
    """Stock valued, or an honest statement that it cannot be.

    `method` is what the organization declared. While it is `UNDECLARED`,
    `rows` still carries every quantity and every `unit_cost` is null: the
    clinic can see what it has without the system inventing what it is worth.
    """

    method: InventoryValuationMethod
    currency: str
    total_value: Decimal | None
    unpriced_items: int
    rows: list[ValuationRow]
