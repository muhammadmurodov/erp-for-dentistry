"""What a stock movement is, and what stock is worth.

The valuation method is the one thing in this module that nobody has
answered. It is declared per organization and defaults to `UNDECLARED`,
exactly as revenue tax treatment is, because what a box of composite is
*worth* on the shelf is an accounting choice and this project does not make
accounting choices on an accountant's behalf.
"""

from __future__ import annotations

from enum import StrEnum


class StockMovementKind(StrEnum):
    """Why a quantity moved.

    Every movement is signed: a positive quantity puts stock on a shelf, a
    negative one takes it off. The kind says *why*, and a check constraint
    keeps the two from disagreeing — a receipt cannot be negative and a
    consumption cannot be positive.
    """

    RECEIPT = "RECEIPT"
    """Goods arrived from a supplier. Created by recording a goods receipt."""

    CONSUMPTION = "CONSUMPTION"
    """Material used on a treatment. The dentist records what they used."""

    ADJUSTMENT = "ADJUSTMENT"
    """A counted difference, after somebody with authority approved it."""

    WRITE_OFF = "WRITE_OFF"
    """Expired, broken or spoiled stock, taken off with a reason."""

    TRANSFER_IN = "TRANSFER_IN"
    """Arrived at this location from another one."""

    TRANSFER_OUT = "TRANSFER_OUT"
    """Left this location for another one."""


#: Kinds that may only ever add stock.
INCREASING_KINDS: frozenset[StockMovementKind] = frozenset(
    {StockMovementKind.RECEIPT, StockMovementKind.TRANSFER_IN}
)

#: Kinds that may only ever remove it.
DECREASING_KINDS: frozenset[StockMovementKind] = frozenset(
    {
        StockMovementKind.CONSUMPTION,
        StockMovementKind.WRITE_OFF,
        StockMovementKind.TRANSFER_OUT,
    }
)


class StockMovementSource(StrEnum):
    """Which record caused the movement, so every quantity is traceable."""

    GOODS_RECEIPT_LINE = "GOODS_RECEIPT_LINE"
    TREATMENT_ITEM = "TREATMENT_ITEM"
    STOCK_ADJUSTMENT = "STOCK_ADJUSTMENT"
    STOCK_TRANSFER = "STOCK_TRANSFER"
    MANUAL = "MANUAL"


class AdjustmentStatus(StrEnum):
    """A counted difference is proposed by one person and approved by another."""

    DRAFT = "DRAFT"
    """Being counted. Nothing has moved."""

    PROPOSED = "PROPOSED"
    """Counting finished, waiting for somebody who may approve it."""

    APPROVED = "APPROVED"
    """Approved: this is the point at which stock actually moves."""

    REJECTED = "REJECTED"
    """Refused, with a reason. The count stays on the record."""


#: An adjustment that has not been decided yet.
OPEN_ADJUSTMENT_STATUSES: frozenset[AdjustmentStatus] = frozenset(
    {AdjustmentStatus.DRAFT, AdjustmentStatus.PROPOSED}
)


class InventoryValuationMethod(StrEnum):
    """How stock on the shelf is costed.

    **Unconfirmed — see `docs/OPEN_QUESTIONS.md` question I1.** Until an
    accountant answers, stock is counted and never costed: quantities are
    tracked, low stock is flagged, expiry is warned about, and every question
    about *value* is refused with the question named.
    """

    UNDECLARED = "UNDECLARED"
    """No method chosen. Quantities work; value is refused."""

    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"
    """Cost is the average the clinic actually paid, from its supplier bills."""

    FIFO = "FIFO"
    """First in, first out. Needs cost layers this system does not keep."""
