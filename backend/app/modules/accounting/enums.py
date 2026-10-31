"""Enumerations used across the accounting engine.

Stored as short strings with a CHECK constraint rather than a native
PostgreSQL enum: adding a value to a native enum needs DDL and cannot be done
inside a transaction with other statements on older servers, and the check
constraint reads just as well in an error message.
"""

from __future__ import annotations

from enum import StrEnum


class AccountLevel(StrEnum):
    """Where a row sits in the statutory hierarchy.

    Only the last three are postable. Posting to a part, section or group
    would double-count it against its own children in a trial balance.
    """

    PART = "PART"
    SECTION = "SECTION"
    GROUP = "GROUP"
    ACCOUNT = "ACCOUNT"
    SUBACCOUNT = "SUBACCOUNT"
    SYNTHETIC = "SYNTHETIC"


POSTABLE_LEVELS = frozenset({AccountLevel.ACCOUNT, AccountLevel.SUBACCOUNT, AccountLevel.SYNTHETIC})


class AccountNature(StrEnum):
    """The statutory debit/credit character of an account."""

    ASSET = "ASSET"  # Актив
    LIABILITY_EQUITY = "LIABILITY_EQUITY"  # Пассив
    CONTRA_ASSET = "CONTRA_ASSET"  # Контрактив
    CONTRA_LIABILITY = "CONTRA_LIABILITY"  # Контрпассив
    TRANSIT = "TRANSIT"  # Транзит
    OFF_BALANCE = "OFF_BALANCE"  # Балансдан ташқари


#: Natures whose balance normally sits on the debit side. Used to present a
#: signed balance, never to decide what a posting does.
DEBIT_NORMAL_NATURES = frozenset(
    {AccountNature.ASSET, AccountNature.CONTRA_LIABILITY, AccountNature.TRANSIT}
)


class AccountElement(StrEnum):
    """Which financial statement a balance belongs to."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"
    RESULT = "RESULT"


#: Elements that close to the result account at period end rather than
#: carrying forward.
TEMPORARY_ELEMENTS = frozenset(
    {AccountElement.REVENUE, AccountElement.EXPENSE, AccountElement.RESULT}
)


class PeriodStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class FiscalYearStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class EntryStatus(StrEnum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"


class PolicyStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class PostingPurpose(StrEnum):
    """Why an entry was posted, as part of its idempotency key.

    One source document can legitimately produce several entries — a card
    payment produces the revenue recognition and, later, the settlement — so
    the key is (source kind, source id, purpose) rather than just the source.
    """

    RECOGNITION = "RECOGNITION"
    SETTLEMENT = "SETTLEMENT"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    ACCRUAL = "ACCRUAL"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"
    OPENING_BALANCE = "OPENING_BALANCE"
    PERIOD_CLOSE = "PERIOD_CLOSE"
    MANUAL = "MANUAL"


class SourceKind(StrEnum):
    """What caused a posting.

    `MANUAL` is the only value that does not refer to another module's record,
    and a manual entry still requires an authorised actor and a reason — see
    accounting invariant 6.
    """

    MANUAL = "MANUAL"
    OPENING_BALANCE = "OPENING_BALANCE"
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    CASH_SESSION = "CASH_SESSION"
    BANK_TRANSACTION = "BANK_TRANSACTION"
    CARD_SETTLEMENT = "CARD_SETTLEMENT"
    SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
    GOODS_RECEIPT = "GOODS_RECEIPT"
    STOCK_MOVEMENT = "STOCK_MOVEMENT"
    PAYROLL_RUN = "PAYROLL_RUN"
    DEPRECIATION = "DEPRECIATION"
    PERIOD_CLOSE = "PERIOD_CLOSE"
