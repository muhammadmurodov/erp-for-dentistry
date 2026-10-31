"""Bank account, statement and reconciliation records.

Two design points carry most of the weight here:

* **A fingerprint per transaction.** Re-importing the same statement file —
  which happens constantly, because people re-download when unsure — must not
  create a second copy of every line. The fingerprint is a digest of the
  fields a bank cannot change between exports.
* **Matching is a join table.** `ReconciliationMatch` links one bank line to
  one internal record, so many rows sharing a bank line express
  "one settlement covers several payments", and many sharing an internal
  record express the reverse. One-to-one, one-to-many and many-to-one all
  fall out without a special case.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.banking.enums import (
    ImportStatus,
    MatchableKind,
    TransactionDirection,
    TransactionStatus,
)


def _enum(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _org_scope(table: str) -> tuple:
    return (UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id"),)


class BankAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An account at a bank, tied to a ledger account.

    `column_mapping` is how this module copes with not knowing the bank's
    export format (docs/OPEN_QUESTIONS.md question B3). It maps this
    system's field names to the spreadsheet's own column headings, so a new
    bank is configuration rather than a code change.
    """

    __tablename__ = "bank_account"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(255))
    #: Uzbek interbank code (МФО).
    mfo: Mapped[str | None] = mapped_column(String(16))
    #: Stored as given. Treated as sensitive: never written to a log, and
    #: shown masked in listings.
    account_number: Mapped[str | None] = mapped_column(String(34))
    account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: `{"value_date": "ДатаОперации", "credit": "Поступление", ...}`
    column_mapping: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        *_org_scope("bank_account"),
        UniqueConstraint("organization_id", "code", name="uq_bank_account_organization_id_code"),
        ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["account.organization_id", "account.id"],
            ondelete="RESTRICT",
            name="fk_bank_account_ledger_account",
        ),
        CheckConstraint("mfo IS NULL OR mfo ~ '^[0-9]{5}$'", name="mfo_is_five_digits"),
    )


class BankStatementImport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One import run, kept so an operator can see what a file actually did."""

    __tablename__ = "bank_statement_import"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bank_account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        _enum(ImportStatus, "bank_import_status"),
        nullable=False,
        default=ImportStatus.COMPLETED,
    )
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Lines already present from a previous import of the same statement.
    duplicates_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Why each rejected row was rejected, so the operator can fix the file.
    problems: Mapped[list | None] = mapped_column(JSONB)
    imported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("bank_statement_import"),
        ForeignKeyConstraint(
            ["organization_id", "bank_account_id"],
            ["bank_account.organization_id", "bank_account.id"],
            ondelete="RESTRICT",
            name="fk_bank_statement_import_account",
        ),
    )


class BankTransaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One line of a bank statement.

    A statement line is **evidence**, not an economic event. Importing it
    posts nothing; matching it to an internal record is what produces an
    accounting consequence, and only where one is due.
    """

    __tablename__ = "bank_transaction"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bank_account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    import_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[TransactionDirection] = mapped_column(
        _enum(TransactionDirection, "bank_transaction_direction"), nullable=False
    )
    #: Always positive; `direction` carries the sign, as with a journal line.
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")

    description: Mapped[str | None] = mapped_column(Text)
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    counterparty_account: Mapped[str | None] = mapped_column(String(34))
    counterparty_mfo: Mapped[str | None] = mapped_column(String(16))
    document_number: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[TransactionStatus] = mapped_column(
        _enum(TransactionStatus, "bank_transaction_status"),
        nullable=False,
        default=TransactionStatus.UNMATCHED,
    )
    ignored_reason: Mapped[str | None] = mapped_column(Text)

    #: What the bank or acquirer withheld from this receipt. Held here rather
    #: than derived from the match rows, because the gross takings and the net
    #: settlement differ by exactly this and the difference has to survive a
    #: second match against the same line.
    commission_amount: Mapped[Decimal] = mapped_column(
        MoneyType, nullable=False, server_default="0"
    )

    #: Digest of the fields a bank cannot change between exports of the same
    #: statement. Unique per account, so re-importing a file adds nothing.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The raw row as read, kept so a mapping mistake can be diagnosed
    #: without the original file.
    raw: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        *_org_scope("bank_transaction"),
        UniqueConstraint(
            "organization_id",
            "bank_account_id",
            "fingerprint",
            name="uq_bank_transaction_fingerprint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "bank_account_id"],
            ["bank_account.organization_id", "bank_account.id"],
            ondelete="RESTRICT",
            name="fk_bank_transaction_account",
        ),
        ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["bank_statement_import.organization_id", "bank_statement_import.id"],
            ondelete="SET NULL",
            name="fk_bank_transaction_import",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("commission_amount >= 0", name="commission_not_negative"),
        CheckConstraint(
            "status <> 'IGNORED' OR ignored_reason IS NOT NULL",
            name="ignoring_needs_a_reason",
        ),
        # An ignored line is a decision to account for nothing, so it must
        # not carry a withheld fee either.
        CheckConstraint(
            "status <> 'IGNORED' OR commission_amount = 0",
            name="ignored_line_has_no_commission",
        ),
        Index(
            "ix_bank_transaction_queue",
            "organization_id",
            "bank_account_id",
            "status",
            "value_date",
        ),
    )


class ReconciliationMatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A link between a bank line and one internal record.

    Several rows sharing a `bank_transaction_id` mean one settlement covered
    several payments. Several sharing a source mean the reverse. The shape
    is what makes one-to-one, one-to-many and many-to-one the same code path.
    """

    __tablename__ = "reconciliation_match"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bank_transaction_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_kind: Mapped[MatchableKind] = mapped_column(
        _enum(MatchableKind, "matchable_kind"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: How much of the bank line this internal record accounts for.
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    matched_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("reconciliation_match"),
        # The same internal record cannot be matched to the same bank line
        # twice, so a repeated request cannot double-count a settlement.
        UniqueConstraint(
            "organization_id",
            "bank_transaction_id",
            "source_kind",
            "source_id",
            name="uq_reconciliation_match_pair",
        ),
        ForeignKeyConstraint(
            ["organization_id", "bank_transaction_id"],
            ["bank_transaction.organization_id", "bank_transaction.id"],
            ondelete="CASCADE",
            name="fk_reconciliation_match_transaction",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index(
            "ix_reconciliation_match_source",
            "organization_id",
            "source_kind",
            "source_id",
        ),
    )


__all__ = [
    "BankAccount",
    "BankStatementImport",
    "BankTransaction",
    "ReconciliationMatch",
]
