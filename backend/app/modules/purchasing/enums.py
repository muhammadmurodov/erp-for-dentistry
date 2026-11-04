"""Enumerations for suppliers, orders, receiving, bills and payments.

The four states the brief insists on keeping apart — ordered, received,
invoiced, paid — are four different records here, not four flags on one. A
single `purchase` row with booleans is how a system ends up unable to answer
"what have we received but not been billed for", which is the question that
finds the supplier who invoices twice.
"""

from __future__ import annotations

from enum import StrEnum


class RequestStatus(StrEnum):
    """Somebody asking for something to be bought."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ORDERED = "ORDERED"
    """An order was raised from it. The request is answered."""

    CANCELLED = "CANCELLED"


class OrderStatus(StrEnum):
    """A commitment to a supplier. **Nothing here posts to the ledger.**

    An order is a promise to buy, not a transaction: no asset has arrived and
    no liability exists until something does. Posting at order time is how a
    cancelled order leaves a phantom payable behind.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SENT = "SENT"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"
    """Deliberately finished short — the rest will not arrive and nobody is
    waiting for it. Distinct from RECEIVED, which means it all came."""


#: Order states in which goods may still be received against the order.
RECEIVABLE_ORDER_STATUSES = frozenset(
    {OrderStatus.APPROVED, OrderStatus.SENT, OrderStatus.PARTIALLY_RECEIVED}
)

#: Order states that are finished, one way or another.
CLOSED_ORDER_STATUSES = frozenset({OrderStatus.RECEIVED, OrderStatus.CANCELLED, OrderStatus.CLOSED})


class BillKind(StrEnum):
    """What a supplier invoice is for, which decides what it debits.

    Two kinds rather than a free choice of account: the clinic buys dental
    materials, which are an asset until they are used, and services such as
    rent, which are an expense when incurred. Those are genuinely different
    postings and the workbook treats them differently. A third kind would
    need a third approved rule, not a wider dropdown.
    """

    MATERIALS = "MATERIALS"
    EXPENSE = "EXPENSE"


class BillStatus(StrEnum):
    DRAFT = "DRAFT"
    """Entered, not yet in the books. Nothing posted, nothing owed."""

    REGISTERED = "REGISTERED"
    """Posted: the payable exists. Immutable from here."""

    VOID = "VOID"
    """Reversed. The payable is gone and the reversal is on the record."""


class SupplierPaymentMethod(StrEnum):
    CASH = "CASH"
    BANK = "BANK"
    CARD = "CARD"


class SupplierPaymentStatus(StrEnum):
    RECORDED = "RECORDED"
    REVERSED = "REVERSED"
