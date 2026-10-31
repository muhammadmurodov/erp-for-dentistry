"""The ledger's tables.

Read `docs/ACCOUNTING_DESIGN.md` before changing anything here. Several
constraints look redundant and are not: the `UNIQUE (organization_id, id)` on
each table exists so that children can carry a *composite* foreign key, which
is what makes a cross-organization journal line unrepresentable rather than
merely rejected.

The balance, immutability and period checks live in database triggers created
by the Phase 2 migration, not in this file. They are listed in each model's
docstring so the enforcement is discoverable from here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.accounting.enums import (
    AccountElement,
    AccountLevel,
    AccountNature,
    EntryStatus,
    FiscalYearStatus,
    PeriodStatus,
    PolicyStatus,
    PostingPurpose,
    SourceKind,
)


def _enum(enum_type: type, name: str) -> Enum:
    """A VARCHAR column with a CHECK constraint, not a native PG enum."""
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _org_scope(table: str) -> tuple:
    """The constraint that lets children carry a composite foreign key."""
    return (UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id"),)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


class FiscalYear(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A reporting year. Contains the periods entries are posted into."""

    __tablename__ = "fiscal_year"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[FiscalYearStatus] = mapped_column(
        _enum(FiscalYearStatus, "fiscal_year_status"),
        nullable=False,
        default=FiscalYearStatus.OPEN,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    periods: Mapped[list[AccountingPeriod]] = relationship(
        back_populates="fiscal_year", lazy="raise_on_sql"
    )

    __table_args__ = (
        *_org_scope("fiscal_year"),
        UniqueConstraint("organization_id", "code", name="uq_fiscal_year_organization_id_code"),
        CheckConstraint("ends_on > starts_on", name="ends_after_start"),
    )


class AccountingPeriod(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A posting window.

    A posted entry must name an OPEN period. The migration adds a trigger
    enforcing that, so closing a period genuinely stops postings rather than
    relying on every service remembering to check.
    """

    __tablename__ = "accounting_period"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        _enum(PeriodStatus, "period_status"), nullable=False, default=PeriodStatus.OPEN
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    #: Why it was reopened, if it was. Reopening is an audited, justified act.
    reopened_reason: Mapped[str | None] = mapped_column(Text)

    fiscal_year: Mapped[FiscalYear] = relationship(back_populates="periods", lazy="raise_on_sql")

    __table_args__ = (
        *_org_scope("accounting_period"),
        UniqueConstraint(
            "organization_id", "code", name="uq_accounting_period_organization_id_code"
        ),
        ForeignKeyConstraint(
            ["organization_id", "fiscal_year_id"],
            ["fiscal_year.organization_id", "fiscal_year.id"],
            ondelete="RESTRICT",
            name="fk_accounting_period_fiscal_year",
        ),
        CheckConstraint("ends_on >= starts_on", name="ends_not_before_start"),
        Index("ix_accounting_period_dates", "organization_id", "starts_on", "ends_on"),
    )


# --------------------------------------------------------------------------
# Chart of accounts
# --------------------------------------------------------------------------


class Account(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row of an organization's chart of accounts.

    Accounts are deactivated, never deleted: a posted line references an
    account forever, and removing one would orphan history.
    """

    __tablename__ = "account"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz: Mapped[str | None] = mapped_column(String(255))
    name_ru: Mapped[str | None] = mapped_column(String(255))

    level: Mapped[AccountLevel] = mapped_column(
        _enum(AccountLevel, "account_level"), nullable=False
    )
    nature: Mapped[AccountNature | None] = mapped_column(_enum(AccountNature, "account_nature"))
    element: Mapped[AccountElement | None] = mapped_column(_enum(AccountElement, "account_element"))
    statutory_part: Mapped[str | None] = mapped_column(String(8))

    parent_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    #: Denormalised from `level`, kept in step by a trigger. A journal line's
    #: foreign key points at (organization_id, id, is_postable), so a line
    #: referencing a non-postable account cannot be stored at all.
    is_postable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: True while the statutory classification is unconfirmed. Seeded accounts
    #: start true; see docs/OPEN_QUESTIONS.md question A1.
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("account"),
        UniqueConstraint("organization_id", "code", name="uq_account_organization_id_code"),
        # The target of journal_line's composite foreign key. Including
        # is_postable in a unique key is what lets that key reject a posting
        # to a structural account in the database rather than in Python.
        UniqueConstraint(
            "organization_id",
            "id",
            "is_postable",
            name="uq_account_organization_id_id_is_postable",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_id"],
            ["account.organization_id", "account.id"],
            ondelete="RESTRICT",
            name="fk_account_parent",
        ),
        CheckConstraint("code = btrim(code) AND char_length(code) > 0", name="code_trimmed"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        Index("ix_account_organization_id_level", "organization_id", "level"),
    )


# --------------------------------------------------------------------------
# Accounting policy
# --------------------------------------------------------------------------


class AccountingPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A versioned, effective-dated set of operation → Dr/Cr mappings.

    A posted entry stores the id of the version that produced it and the
    resolved account ids, so editing a policy never changes what a historical
    entry says. That is the defect this design exists to avoid — see ADR-011.
    """

    __tablename__ = "accounting_policy"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[PolicyStatus] = mapped_column(
        _enum(PolicyStatus, "policy_status"), nullable=False, default=PolicyStatus.DRAFT
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL means "still in force".
    effective_to: Mapped[date | None] = mapped_column(Date)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    notes: Mapped[str | None] = mapped_column(Text)

    rules: Mapped[list[PolicyRule]] = relationship(
        back_populates="policy", cascade="all, delete-orphan", lazy="raise_on_sql"
    )

    __table_args__ = (
        *_org_scope("accounting_policy"),
        UniqueConstraint(
            "organization_id",
            "code",
            "version",
            name="uq_accounting_policy_organization_id_code_version",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_ordered",
        ),
    )


class PolicyRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One operation type mapped to a debit and a credit account.

    The direct descendant of the legacy workbook's `Учетная_политика` sheet,
    with the properties that sheet lacked: owned by one organization, tied to
    a policy version, and validated before the version is activated.
    """

    __tablename__ = "policy_rule"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    operation_code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    description_uz: Mapped[str | None] = mapped_column(Text)
    description_ru: Mapped[str | None] = mapped_column(Text)
    debit_account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    credit_account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: Source documents the operation is expected to be supported by. Free
    #: text carried over from the workbook; informational, not enforced.
    required_documents: Mapped[str | None] = mapped_column(Text)

    policy: Mapped[AccountingPolicy] = relationship(back_populates="rules", lazy="raise_on_sql")

    __table_args__ = (
        *_org_scope("policy_rule"),
        UniqueConstraint(
            "organization_id",
            "policy_id",
            "operation_code",
            name="uq_policy_rule_organization_id_policy_id_operation_code",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["accounting_policy.organization_id", "accounting_policy.id"],
            ondelete="CASCADE",
            name="fk_policy_rule_policy",
        ),
        # Both sides must be postable accounts of the same organization.
        ForeignKeyConstraint(
            ["organization_id", "debit_account_id"],
            ["account.organization_id", "account.id"],
            ondelete="RESTRICT",
            name="fk_policy_rule_debit_account",
        ),
        ForeignKeyConstraint(
            ["organization_id", "credit_account_id"],
            ["account.organization_id", "account.id"],
            ondelete="RESTRICT",
            name="fk_policy_rule_credit_account",
        ),
        CheckConstraint("debit_account_id <> credit_account_id", name="debit_differs_from_credit"),
    )


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


class PostingEvent(Base, UUIDPrimaryKeyMixin):
    """The record that makes posting idempotent.

    `UNIQUE (organization_id, source_kind, source_id, purpose)` is accounting
    invariant 9. Two concurrent attempts to post the same business event both
    insert here; one commits and the other fails on the constraint. That works
    regardless of timing, which a check-then-act cannot.
    """

    __tablename__ = "posting_event"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_kind: Mapped[SourceKind] = mapped_column(
        _enum(SourceKind, "source_kind"), nullable=False
    )
    #: The originating record's identifier, as text so any module's key fits.
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[PostingPurpose] = mapped_column(
        _enum(PostingPurpose, "posting_purpose"), nullable=False
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("posting_event"),
        UniqueConstraint(
            "organization_id",
            "source_kind",
            "source_id",
            "purpose",
            name="uq_posting_event_source",
        ),
        Index("ix_posting_event_journal_entry_id", "journal_entry_id"),
    )


class JournalEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A balanced set of journal lines.

    Database enforcement added by the migration:

    * `trg_journal_entry_immutable` — a POSTED entry cannot be edited or
      deleted; only the reversal links may change (invariants 4 and 5).
    * `trg_journal_entry_balanced` — a deferred constraint trigger that
      re-checks debits == credits at COMMIT (invariants 1 and 2).
    * `trg_journal_entry_period_open` — a POSTED entry must name an OPEN
      period (invariant 8).
    """

    __tablename__ = "journal_entry"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Human-facing, unique per organization (invariant 7).
    entry_number: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    status: Mapped[EntryStatus] = mapped_column(
        _enum(EntryStatus, "entry_status"), nullable=False, default=EntryStatus.DRAFT
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")

    #: What caused this entry (invariant 6). A manual entry records MANUAL and
    #: must carry a reason.
    source_kind: Mapped[SourceKind] = mapped_column(
        _enum(SourceKind, "source_kind"), nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(String(128))
    #: The policy version that resolved the accounts, when a policy was used.
    #: Stored so that editing the policy later cannot restate this entry.
    policy_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    operation_code: Mapped[str | None] = mapped_column(String(64))

    reversal_of_entry_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reversed_by_entry_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: Required when reversing; recorded on the reversing entry.
    reason: Mapped[str | None] = mapped_column(Text)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    lines: Mapped[list[JournalLine]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
        order_by="JournalLine.line_number",
    )

    __table_args__ = (
        *_org_scope("journal_entry"),
        UniqueConstraint(
            "organization_id", "entry_number", name="uq_journal_entry_organization_id_entry_number"
        ),
        ForeignKeyConstraint(
            ["organization_id", "period_id"],
            ["accounting_period.organization_id", "accounting_period.id"],
            ondelete="RESTRICT",
            name="fk_journal_entry_period",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reversal_of_entry_id"],
            ["journal_entry.organization_id", "journal_entry.id"],
            ondelete="RESTRICT",
            name="fk_journal_entry_reversal_of",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reversed_by_entry_id"],
            ["journal_entry.organization_id", "journal_entry.id"],
            ondelete="RESTRICT",
            name="fk_journal_entry_reversed_by",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["accounting_policy.organization_id", "accounting_policy.id"],
            ondelete="RESTRICT",
            name="fk_journal_entry_policy",
        ),
        CheckConstraint("status = 'POSTED' OR posted_at IS NULL", name="only_posted_has_posted_at"),
        CheckConstraint("status <> 'POSTED' OR posted_at IS NOT NULL", name="posted_has_posted_at"),
        CheckConstraint("currency = upper(currency)", name="currency_uppercase"),
        CheckConstraint(
            "reversal_of_entry_id IS NULL OR reversal_of_entry_id <> id",
            name="not_own_reversal",
        ),
        Index("ix_journal_entry_organization_id_entry_date", "organization_id", "entry_date"),
        Index("ix_journal_entry_organization_id_status", "organization_id", "status"),
        Index("ix_journal_entry_source", "organization_id", "source_kind", "source_id"),
    )


class JournalLine(Base, UUIDPrimaryKeyMixin):
    """One debit or credit against one account.

    Exactly one side carries a positive amount. Negative amounts are refused:
    "credit 500" and "debit -500" are the same fact with two spellings, and
    allowing both means every report has to handle both.

    The composite foreign key to `account` includes `is_postable`, so a line
    against a structural account (a part, section or group) cannot be stored.
    """

    __tablename__ = "journal_line"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: Always True. Present only so the composite foreign key below can
    #: require the referenced account to be postable.
    #: Mirror of `account.is_postable`, filled by a trigger from the referenced
    #: account — never by the caller. Together with the composite foreign key
    #: and the CHECK requiring it to be true, that makes a posting to a
    #: structural account impossible rather than merely rejected.
    account_is_postable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    debit: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("0"))
    description: Mapped[str | None] = mapped_column(Text)

    #: Denormalised from the entry so the general ledger and trial balance can
    #: be computed without joining, and so an index can serve them directly.
    #: Kept in step by a trigger; a posted entry is immutable, so it cannot
    #: drift afterwards.
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    entry_status: Mapped[EntryStatus] = mapped_column(
        _enum(EntryStatus, "entry_status"), nullable=False
    )

    entry: Mapped[JournalEntry] = relationship(back_populates="lines", lazy="raise_on_sql")

    __table_args__ = (
        *_org_scope("journal_line"),
        UniqueConstraint(
            "organization_id",
            "journal_entry_id",
            "line_number",
            name="uq_journal_line_entry_line_number",
        ),
        # Invariant 3: a line whose organization differs from its entry's is
        # unrepresentable, not merely rejected by application code.
        ForeignKeyConstraint(
            ["organization_id", "journal_entry_id"],
            ["journal_entry.organization_id", "journal_entry.id"],
            ondelete="CASCADE",
            name="fk_journal_line_entry",
        ),
        ForeignKeyConstraint(
            ["organization_id", "account_id", "account_is_postable"],
            ["account.organization_id", "account.id", "account.is_postable"],
            ondelete="RESTRICT",
            name="fk_journal_line_account_postable",
        ),
        CheckConstraint("debit >= 0 AND credit >= 0", name="amounts_not_negative"),
        CheckConstraint("NOT (debit > 0 AND credit > 0)", name="one_side_only"),
        CheckConstraint("debit > 0 OR credit > 0", name="no_zero_line"),
        CheckConstraint("account_is_postable", name="account_must_be_postable"),
        CheckConstraint("line_number >= 1", name="line_number_positive"),
        # The access path for the general ledger, account statements and the
        # trial balance.
        Index(
            "ix_journal_line_ledger",
            "organization_id",
            "account_id",
            "entry_status",
            "entry_date",
        ),
        Index("ix_journal_line_period", "organization_id", "period_id", "entry_status"),
    )


class NumberSequence(Base, TimestampMixin):
    """Per-organization document numbering.

    A row is locked with `SELECT ... FOR UPDATE` while a number is taken, so
    two concurrent postings cannot receive the same entry number. A PostgreSQL
    sequence would be faster but is not transactional — a rolled-back posting
    would leave a gap, and an accountant reading a ledger with missing numbers
    has to prove nothing was deleted.
    """

    __tablename__ = "number_sequence"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    padding: Mapped[int] = mapped_column(Integer, nullable=False, default=6)

    __table_args__ = (
        CheckConstraint("next_value >= 1", name="next_value_positive"),
        CheckConstraint("padding BETWEEN 1 AND 12", name="padding_in_range"),
    )


__all__ = [
    "Account",
    "AccountingPeriod",
    "AccountingPolicy",
    "FiscalYear",
    "JournalEntry",
    "JournalLine",
    "NumberSequence",
    "PolicyRule",
    "PostingEvent",
]
