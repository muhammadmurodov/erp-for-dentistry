"""Enumerations for banking and reconciliation."""

from __future__ import annotations

from enum import StrEnum


class TransactionDirection(StrEnum):
    """Which way the money went, from the account's point of view."""

    IN = "IN"
    OUT = "OUT"


class TransactionStatus(StrEnum):
    UNMATCHED = "UNMATCHED"
    """Imported and not yet explained. Sits in the queue until it is."""

    MATCHED = "MATCHED"
    """Linked to one or more internal records that account for it."""

    IGNORED = "IGNORED"
    """Deliberately set aside, with a reason. Never silently dropped."""


class MatchableKind(StrEnum):
    """What a bank line can be matched against.

    Each names a record this system already holds, so a match is always a
    link between two known things rather than a free-text annotation.
    """

    CASH_DEPOSIT = "CASH_DEPOSIT"
    """A `CashMovement` of kind DEPOSIT, awaiting bank confirmation."""

    CARD_PAYMENT = "CARD_PAYMENT"
    """A `Payment` taken by card, awaiting the acquirer's settlement."""

    BANK_PAYMENT = "BANK_PAYMENT"
    """A `Payment` the customer made by transfer."""


class ImportStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
