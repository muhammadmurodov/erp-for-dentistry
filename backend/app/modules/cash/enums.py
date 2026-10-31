"""Enumerations for cash handling."""

from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    OPEN = "OPEN"
    """Taking money. Exactly one session per register may be open."""

    COUNTED = "COUNTED"
    """A physical count has been entered but the session is not yet closed.

    A session with a discrepancy stays here until somebody with the authority
    to approve it does so — which is how a shortfall becomes a decision
    rather than a rounding.
    """

    CLOSED = "CLOSED"
    """Reconciled and posted. No further movement is possible."""


class MovementKind(StrEnum):
    """What moved the cash.

    Sign is **not** carried on the amount: every amount is positive and the
    kind decides its direction, for the same reason a journal line is a debit
    or a credit rather than a signed number.
    """

    OPENING_FLOAT = "OPENING_FLOAT"
    RECEIPT = "RECEIPT"
    REFUND = "REFUND"
    EXPENSE = "EXPENSE"
    DEPOSIT = "DEPOSIT"
    """Cash sent to the bank — via staff or via collection."""

    ADJUSTMENT_IN = "ADJUSTMENT_IN"
    ADJUSTMENT_OUT = "ADJUSTMENT_OUT"


#: Kinds that increase the cash in the drawer.
INFLOW_KINDS = frozenset(
    {MovementKind.OPENING_FLOAT, MovementKind.RECEIPT, MovementKind.ADJUSTMENT_IN}
)
#: Kinds that reduce it.
OUTFLOW_KINDS = frozenset(
    {
        MovementKind.REFUND,
        MovementKind.EXPENSE,
        MovementKind.DEPOSIT,
        MovementKind.ADJUSTMENT_OUT,
    }
)


class DepositRoute(StrEnum):
    """How cash physically reaches the bank.

    The clinic's workbook distinguishes these and posts them to different
    transit accounts, because the money is in different hands and is at risk
    for different lengths of time.
    """

    STAFF = "STAFF"
    """Carried in by an employee (workbook account 5710.01)."""

    COLLECTION = "COLLECTION"
    """Collected by the bank's cash-in-transit service (5710.02)."""
