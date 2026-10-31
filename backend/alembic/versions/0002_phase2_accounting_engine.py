"""phase 2 accounting engine

Revision ID: 738cbc2d61cf
Revises: 9218b24fa226
Create Date: 2026-10-30 14:46:49.587633
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "738cbc2d61cf"
down_revision: str | None = "9218b24fa226"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Database-level accounting invariants.
#
# These exist because an application bug must not be able to produce an
# unbalanced or retroactively edited ledger. The posting service checks the
# same rules and produces better error messages; these are the guarantee.
#
# Numbering matches docs/ACCOUNTING_DESIGN.md.
# ---------------------------------------------------------------------------

#: Invariants 1 and 2 — a posted entry balances, and its totals are equal.
#:
#: Balance is a property of a *set* of rows, so it cannot be a CHECK. This is
#: a DEFERRABLE INITIALLY DEFERRED constraint trigger: it fires at COMMIT, by
#: which time every line exists. That also lets a service insert the entry and
#: its lines in any order.
BALANCE_FUNCTION = """
CREATE OR REPLACE FUNCTION assert_journal_entry_balanced()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_entry_id   uuid;
    v_status     text;
    v_number     text;
    v_debit      numeric(20,4);
    v_credit     numeric(20,4);
    v_line_count integer;
BEGIN
    -- Branch with IF rather than a CASE over record fields: PL/pgSQL
    -- type-checks every branch of a CASE against the row type, and
    -- `journal_entry` has no `journal_entry_id` column.
    IF TG_TABLE_NAME = 'journal_entry' THEN
        IF TG_OP = 'DELETE' THEN
            v_entry_id := OLD.id;
        ELSE
            v_entry_id := NEW.id;
        END IF;
    ELSE
        IF TG_OP = 'DELETE' THEN
            v_entry_id := OLD.journal_entry_id;
        ELSE
            v_entry_id := NEW.journal_entry_id;
        END IF;
    END IF;

    IF v_entry_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT status, entry_number INTO v_status, v_number
    FROM journal_entry WHERE id = v_entry_id;

    -- The entry was deleted in this transaction (only possible for a draft).
    IF v_status IS NULL THEN
        RETURN NULL;
    END IF;

    -- A draft is work in progress and is allowed to be incomplete.
    IF v_status <> 'POSTED' THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0), COUNT(*)
    INTO v_debit, v_credit, v_line_count
    FROM journal_line WHERE journal_entry_id = v_entry_id;

    IF v_line_count < 2 THEN
        RAISE EXCEPTION
            'journal entry % is posted with % line(s); a balanced entry needs at least 2',
            v_number, v_line_count
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_debit <> v_credit THEN
        RAISE EXCEPTION
            'journal entry % does not balance: debits % <> credits % (difference %)',
            v_number, v_debit, v_credit, (v_debit - v_credit)
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_debit = 0 THEN
        RAISE EXCEPTION
            'journal entry % is posted with a zero total', v_number
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;
"""

BALANCE_TRIGGERS = """
CREATE CONSTRAINT TRIGGER trg_journal_entry_balanced
AFTER INSERT OR UPDATE ON journal_entry
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_journal_entry_balanced();

CREATE CONSTRAINT TRIGGER trg_journal_line_balanced
AFTER INSERT OR UPDATE OR DELETE ON journal_line
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_journal_entry_balanced();
"""

#: Invariants 4 and 5 — a posted entry is immutable; corrections are
#: reversals. The only columns that may change after posting are the reversal
#: links, which is how a reversal records itself against its original.
ENTRY_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION journal_entry_is_immutable_once_posted()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'POSTED' THEN
            RAISE EXCEPTION
                'journal entry % is posted and cannot be deleted; reverse it instead',
                OLD.entry_number
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status <> 'POSTED' THEN
        RETURN NEW;
    END IF;

    IF NEW.status <> 'POSTED' THEN
        RAISE EXCEPTION
            'journal entry % is posted and cannot be returned to %',
            OLD.entry_number, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF (NEW.organization_id,  NEW.entry_number, NEW.entry_date, NEW.period_id,
        NEW.description,      NEW.currency,     NEW.source_kind, NEW.source_id,
        NEW.policy_id,        NEW.operation_code,
        NEW.posted_at,        NEW.posted_by_user_id,
        NEW.reversal_of_entry_id)
       IS DISTINCT FROM
       (OLD.organization_id,  OLD.entry_number, OLD.entry_date, OLD.period_id,
        OLD.description,      OLD.currency,     OLD.source_kind, OLD.source_id,
        OLD.policy_id,        OLD.operation_code,
        OLD.posted_at,        OLD.posted_by_user_id,
        OLD.reversal_of_entry_id)
    THEN
        RAISE EXCEPTION
            'journal entry % is posted and cannot be edited; reverse it and post a replacement',
            OLD.entry_number
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_journal_entry_immutable
BEFORE UPDATE OR DELETE ON journal_entry
FOR EACH ROW EXECUTE FUNCTION journal_entry_is_immutable_once_posted();
"""

#: The same protection for the lines. Without it, a posted entry's amounts
#: could be changed underneath it and the balance trigger would still pass.
LINE_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION journal_line_is_immutable_once_posted()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
    v_number text;
BEGIN
    SELECT status, entry_number INTO v_status, v_number
    FROM journal_entry
    WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.journal_entry_id
                          ELSE NEW.journal_entry_id END;

    -- The parent is being deleted in the same statement (a draft cascade).
    IF v_status IS NULL THEN
        RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    IF v_status <> 'POSTED' THEN
        RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    -- Promoting a draft flips entry_status on each line; that one column may
    -- change, and only in that direction.
    IF TG_OP = 'UPDATE'
       AND OLD.entry_status = 'DRAFT' AND NEW.entry_status = 'POSTED'
       AND (NEW.account_id, NEW.debit, NEW.credit, NEW.line_number,
            NEW.description, NEW.entry_date, NEW.period_id)
           IS NOT DISTINCT FROM
           (OLD.account_id, OLD.debit, OLD.credit, OLD.line_number,
            OLD.description, OLD.entry_date, OLD.period_id)
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'journal entry % is posted; its lines cannot be %',
        v_number, CASE TG_OP WHEN 'DELETE' THEN 'deleted' ELSE 'edited' END
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER trg_journal_line_immutable
BEFORE UPDATE OR DELETE ON journal_line
FOR EACH ROW EXECUTE FUNCTION journal_line_is_immutable_once_posted();
"""

#: Invariant 8 — a closed period rejects postings, whoever attempts them.
PERIOD_GUARD = """
CREATE OR REPLACE FUNCTION assert_posting_period_open()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_period_status text;
    v_period_code   text;
BEGIN
    IF NEW.status <> 'POSTED' THEN
        RETURN NEW;
    END IF;

    -- Nothing changed about where this entry sits, so an already-posted entry
    -- recording its reversal link is not re-checked.
    IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND OLD.period_id = NEW.period_id THEN
        RETURN NEW;
    END IF;

    SELECT status, code INTO v_period_status, v_period_code
    FROM accounting_period WHERE id = NEW.period_id;

    IF v_period_status IS NULL THEN
        RAISE EXCEPTION 'journal entry % names a period that does not exist',
            NEW.entry_number USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF v_period_status <> 'OPEN' THEN
        RAISE EXCEPTION
            'accounting period % is closed and cannot accept entry %',
            v_period_code, NEW.entry_number
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.entry_date < (SELECT starts_on FROM accounting_period WHERE id = NEW.period_id)
       OR NEW.entry_date > (SELECT ends_on FROM accounting_period WHERE id = NEW.period_id)
    THEN
        RAISE EXCEPTION
            'entry % is dated % which is outside period %',
            NEW.entry_number, NEW.entry_date, v_period_code
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_journal_entry_period_open
BEFORE INSERT OR UPDATE ON journal_entry
FOR EACH ROW EXECUTE FUNCTION assert_posting_period_open();
"""

#: Keeps `account.is_postable` consistent with `account.level`. The column is
#: denormalised so that journal_line's composite foreign key can require it;
#: letting the two disagree would defeat that key.
ACCOUNT_POSTABLE_SYNC = """
CREATE OR REPLACE FUNCTION sync_account_is_postable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.is_postable := NEW.level IN ('ACCOUNT', 'SUBACCOUNT', 'SYNTHETIC');
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_account_is_postable
BEFORE INSERT OR UPDATE OF level ON account
FOR EACH ROW EXECUTE FUNCTION sync_account_is_postable();
"""

#: Keeps each line's denormalised entry_date/period_id/entry_status in step
#: with its entry. Those columns exist so the ledger and trial balance can be
#: served from one index without a join.
LINE_DENORMALISATION = """
CREATE OR REPLACE FUNCTION sync_journal_line_from_entry()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    SELECT entry_date, period_id, status
    INTO NEW.entry_date, NEW.period_id, NEW.entry_status
    FROM journal_entry WHERE id = NEW.journal_entry_id;

    -- Derived from the account, never from the caller. Combined with the
    -- composite foreign key and the CHECK that requires it to be true, this
    -- is what makes a posting to a part, section or group impossible: a
    -- caller cannot assert postability the account does not have.
    SELECT is_postable INTO NEW.account_is_postable
    FROM account WHERE id = NEW.account_id;

    IF NEW.account_is_postable IS NULL THEN
        RAISE EXCEPTION 'journal line references account % which does not exist',
            NEW.account_id USING ERRCODE = 'foreign_key_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_journal_line_denormalised
BEFORE INSERT ON journal_line
FOR EACH ROW EXECUTE FUNCTION sync_journal_line_from_entry();

CREATE OR REPLACE FUNCTION cascade_entry_status_to_lines()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        UPDATE journal_line
        SET entry_status = NEW.status
        WHERE journal_entry_id = NEW.id AND entry_status IS DISTINCT FROM NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_journal_entry_status_cascade
AFTER UPDATE OF status ON journal_entry
FOR EACH ROW EXECUTE FUNCTION cascade_entry_status_to_lines();
"""

ALL_TRIGGERS = (
    BALANCE_FUNCTION,
    BALANCE_TRIGGERS,
    ENTRY_IMMUTABILITY,
    LINE_IMMUTABILITY,
    PERIOD_GUARD,
    ACCOUNT_POSTABLE_SYNC,
    LINE_DENORMALISATION,
)

DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS trg_journal_entry_status_cascade ON journal_entry;
DROP TRIGGER IF EXISTS trg_journal_line_denormalised ON journal_line;
DROP TRIGGER IF EXISTS trg_account_is_postable ON account;
DROP TRIGGER IF EXISTS trg_journal_entry_period_open ON journal_entry;
DROP TRIGGER IF EXISTS trg_journal_line_immutable ON journal_line;
DROP TRIGGER IF EXISTS trg_journal_entry_immutable ON journal_entry;
DROP TRIGGER IF EXISTS trg_journal_line_balanced ON journal_line;
DROP TRIGGER IF EXISTS trg_journal_entry_balanced ON journal_entry;
DROP FUNCTION IF EXISTS cascade_entry_status_to_lines();
DROP FUNCTION IF EXISTS sync_journal_line_from_entry();
DROP FUNCTION IF EXISTS sync_account_is_postable();
DROP FUNCTION IF EXISTS assert_posting_period_open();
DROP FUNCTION IF EXISTS journal_line_is_immutable_once_posted();
DROP FUNCTION IF EXISTS journal_entry_is_immutable_once_posted();
DROP FUNCTION IF EXISTS assert_journal_entry_balanced();
"""


def upgrade() -> None:
    # ### schema (generated from the SQLAlchemy models) ###
    op.create_table(
        "account",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_uz", sa.String(length=255), nullable=True),
        sa.Column("name_ru", sa.String(length=255), nullable=True),
        sa.Column(
            "level",
            sa.Enum(
                "PART",
                "SECTION",
                "GROUP",
                "ACCOUNT",
                "SUBACCOUNT",
                "SYNTHETIC",
                name="account_level",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "nature",
            sa.Enum(
                "ASSET",
                "LIABILITY_EQUITY",
                "CONTRA_ASSET",
                "CONTRA_LIABILITY",
                "TRANSIT",
                "OFF_BALANCE",
                name="account_nature",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column(
            "element",
            sa.Enum(
                "ASSET",
                "LIABILITY",
                "EQUITY",
                "REVENUE",
                "EXPENSE",
                "RESULT",
                name="account_element",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("statutory_part", sa.String(length=8), nullable=True),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("is_postable", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("needs_confirmation", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code = btrim(code) AND char_length(code) > 0", name=op.f("ck_account_code_trimmed")
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name=op.f("ck_account_not_own_parent")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_id"],
            ["account.organization_id", "account.id"],
            name="fk_account_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_account_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
        sa.UniqueConstraint("organization_id", "code", name="uq_account_organization_id_code"),
        sa.UniqueConstraint(
            "organization_id", "id", "is_postable", name="uq_account_organization_id_id_is_postable"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_account_organization_id_id"),
    )
    op.create_index(
        op.f("ix_account_organization_id"), "account", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_account_organization_id_level", "account", ["organization_id", "level"], unique=False
    )
    op.create_table(
        "accounting_policy",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT", "ACTIVE", "SUPERSEDED", name="policy_status", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_accounting_policy_effective_range_ordered"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_accounting_policy_version_positive")),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_accounting_policy_activated_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_accounting_policy_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounting_policy")),
        sa.UniqueConstraint(
            "organization_id",
            "code",
            "version",
            name="uq_accounting_policy_organization_id_code_version",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_accounting_policy_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_accounting_policy_organization_id"),
        "accounting_policy",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "fiscal_year",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "CLOSED", name="fiscal_year_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_on > starts_on", name=op.f("ck_fiscal_year_ends_after_start")),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_fiscal_year_closed_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_fiscal_year_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fiscal_year")),
        sa.UniqueConstraint("organization_id", "code", name="uq_fiscal_year_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fiscal_year_organization_id_id"),
    )
    op.create_index(
        op.f("ix_fiscal_year_organization_id"), "fiscal_year", ["organization_id"], unique=False
    )
    op.create_table(
        "number_sequence",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.Column("padding", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("next_value >= 1", name=op.f("ck_number_sequence_next_value_positive")),
        sa.CheckConstraint(
            "padding BETWEEN 1 AND 12", name=op.f("ck_number_sequence_padding_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_number_sequence_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("organization_id", "name", name=op.f("pk_number_sequence")),
    )
    op.create_table(
        "posting_event",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum(
                "MANUAL",
                "OPENING_BALANCE",
                "INVOICE",
                "PAYMENT",
                "REFUND",
                "CASH_SESSION",
                "BANK_TRANSACTION",
                "CARD_SETTLEMENT",
                "SUPPLIER_INVOICE",
                "GOODS_RECEIPT",
                "STOCK_MOVEMENT",
                "PAYROLL_RUN",
                "DEPRECIATION",
                "PERIOD_CLOSE",
                name="source_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "RECOGNITION",
                "SETTLEMENT",
                "PAYMENT",
                "REFUND",
                "ACCRUAL",
                "ADJUSTMENT",
                "REVERSAL",
                "OPENING_BALANCE",
                "PERIOD_CLOSE",
                "MANUAL",
                name="posting_purpose",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("journal_entry_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_posting_event_created_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_posting_event_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_posting_event")),
        sa.UniqueConstraint("organization_id", "id", name="uq_posting_event_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "source_kind", "source_id", "purpose", name="uq_posting_event_source"
        ),
    )
    op.create_index(
        "ix_posting_event_journal_entry_id", "posting_event", ["journal_entry_id"], unique=False
    )
    op.create_index(
        op.f("ix_posting_event_organization_id"), "posting_event", ["organization_id"], unique=False
    )
    op.create_table(
        "accounting_period",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("fiscal_year_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "CLOSED", name="period_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reopened_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ends_on >= starts_on", name=op.f("ck_accounting_period_ends_not_before_start")
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_accounting_period_closed_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "fiscal_year_id"],
            ["fiscal_year.organization_id", "fiscal_year.id"],
            name="fk_accounting_period_fiscal_year",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_accounting_period_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounting_period")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_accounting_period_organization_id_code"
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_accounting_period_organization_id_id"
        ),
    )
    op.create_index(
        "ix_accounting_period_dates",
        "accounting_period",
        ["organization_id", "starts_on", "ends_on"],
        unique=False,
    )
    op.create_index(
        op.f("ix_accounting_period_organization_id"),
        "accounting_period",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "policy_rule",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("policy_id", sa.UUID(), nullable=False),
        sa.Column("operation_code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("description_uz", sa.Text(), nullable=True),
        sa.Column("description_ru", sa.Text(), nullable=True),
        sa.Column("debit_account_id", sa.UUID(), nullable=False),
        sa.Column("credit_account_id", sa.UUID(), nullable=False),
        sa.Column("required_documents", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "debit_account_id <> credit_account_id",
            name=op.f("ck_policy_rule_debit_differs_from_credit"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credit_account_id"],
            ["account.organization_id", "account.id"],
            name="fk_policy_rule_credit_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "debit_account_id"],
            ["account.organization_id", "account.id"],
            name="fk_policy_rule_debit_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["accounting_policy.organization_id", "accounting_policy.id"],
            name="fk_policy_rule_policy",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_policy_rule_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_rule")),
        sa.UniqueConstraint("organization_id", "id", name="uq_policy_rule_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "policy_id",
            "operation_code",
            name="uq_policy_rule_organization_id_policy_id_operation_code",
        ),
    )
    op.create_index(
        op.f("ix_policy_rule_organization_id"), "policy_rule", ["organization_id"], unique=False
    )
    op.create_table(
        "journal_entry",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("entry_number", sa.String(length=32), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "POSTED", name="entry_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum(
                "MANUAL",
                "OPENING_BALANCE",
                "INVOICE",
                "PAYMENT",
                "REFUND",
                "CASH_SESSION",
                "BANK_TRANSACTION",
                "CARD_SETTLEMENT",
                "SUPPLIER_INVOICE",
                "GOODS_RECEIPT",
                "STOCK_MOVEMENT",
                "PAYROLL_RUN",
                "DEPRECIATION",
                "PERIOD_CLOSE",
                name="source_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("policy_id", sa.UUID(), nullable=True),
        sa.Column("operation_code", sa.String(length=64), nullable=True),
        sa.Column("reversal_of_entry_id", sa.UUID(), nullable=True),
        sa.Column("reversed_by_entry_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'POSTED' OR posted_at IS NOT NULL",
            name=op.f("ck_journal_entry_posted_has_posted_at"),
        ),
        sa.CheckConstraint(
            "status = 'POSTED' OR posted_at IS NULL",
            name=op.f("ck_journal_entry_only_posted_has_posted_at"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_journal_entry_currency_uppercase")
        ),
        sa.CheckConstraint(
            "reversal_of_entry_id IS NULL OR reversal_of_entry_id <> id",
            name=op.f("ck_journal_entry_not_own_reversal"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_journal_entry_created_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "period_id"],
            ["accounting_period.organization_id", "accounting_period.id"],
            name="fk_journal_entry_period",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["accounting_policy.organization_id", "accounting_policy.id"],
            name="fk_journal_entry_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reversal_of_entry_id"],
            ["journal_entry.organization_id", "journal_entry.id"],
            name="fk_journal_entry_reversal_of",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reversed_by_entry_id"],
            ["journal_entry.organization_id", "journal_entry.id"],
            name="fk_journal_entry_reversed_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_journal_entry_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["posted_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_journal_entry_posted_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entry")),
        sa.UniqueConstraint(
            "organization_id", "entry_number", name="uq_journal_entry_organization_id_entry_number"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_journal_entry_organization_id_id"),
    )
    op.create_index(
        op.f("ix_journal_entry_organization_id"), "journal_entry", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_journal_entry_organization_id_entry_date",
        "journal_entry",
        ["organization_id", "entry_date"],
        unique=False,
    )
    op.create_index(
        "ix_journal_entry_organization_id_status",
        "journal_entry",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_journal_entry_source",
        "journal_entry",
        ["organization_id", "source_kind", "source_id"],
        unique=False,
    )
    op.create_table(
        "journal_line",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("journal_entry_id", sa.UUID(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column(
            "account_is_postable",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("debit", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("credit", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("period_id", sa.UUID(), nullable=False),
        sa.Column(
            "entry_status",
            sa.Enum("DRAFT", "POSTED", name="entry_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "NOT (debit > 0 AND credit > 0)", name=op.f("ck_journal_line_one_side_only")
        ),
        sa.CheckConstraint(
            "account_is_postable", name=op.f("ck_journal_line_account_must_be_postable")
        ),
        sa.CheckConstraint("debit > 0 OR credit > 0", name=op.f("ck_journal_line_no_zero_line")),
        sa.CheckConstraint(
            "debit >= 0 AND credit >= 0", name=op.f("ck_journal_line_amounts_not_negative")
        ),
        sa.CheckConstraint("line_number >= 1", name=op.f("ck_journal_line_line_number_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id", "account_is_postable"],
            ["account.organization_id", "account.id", "account.is_postable"],
            name="fk_journal_line_account_postable",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "journal_entry_id"],
            ["journal_entry.organization_id", "journal_entry.id"],
            name="fk_journal_line_entry",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_journal_line_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_line")),
        sa.UniqueConstraint("organization_id", "id", name="uq_journal_line_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "journal_entry_id",
            "line_number",
            name="uq_journal_line_entry_line_number",
        ),
    )
    op.create_index(
        "ix_journal_line_ledger",
        "journal_line",
        ["organization_id", "account_id", "entry_status", "entry_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_journal_line_organization_id"), "journal_line", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_journal_line_period",
        "journal_line",
        ["organization_id", "period_id", "entry_status"],
        unique=False,
    )
    # ### end generated schema ###

    # The invariants that must not depend on application code being correct.
    for statement in ALL_TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(DROP_TRIGGERS)

    # ### schema (generated from the SQLAlchemy models) ###
    op.drop_index("ix_journal_line_period", table_name="journal_line")
    op.drop_index(op.f("ix_journal_line_organization_id"), table_name="journal_line")
    op.drop_index("ix_journal_line_ledger", table_name="journal_line")
    op.drop_table("journal_line")
    op.drop_index("ix_journal_entry_source", table_name="journal_entry")
    op.drop_index("ix_journal_entry_organization_id_status", table_name="journal_entry")
    op.drop_index("ix_journal_entry_organization_id_entry_date", table_name="journal_entry")
    op.drop_index(op.f("ix_journal_entry_organization_id"), table_name="journal_entry")
    op.drop_table("journal_entry")
    op.drop_index(op.f("ix_policy_rule_organization_id"), table_name="policy_rule")
    op.drop_table("policy_rule")
    op.drop_index(op.f("ix_accounting_period_organization_id"), table_name="accounting_period")
    op.drop_index("ix_accounting_period_dates", table_name="accounting_period")
    op.drop_table("accounting_period")
    op.drop_index(op.f("ix_posting_event_organization_id"), table_name="posting_event")
    op.drop_index("ix_posting_event_journal_entry_id", table_name="posting_event")
    op.drop_table("posting_event")
    op.drop_table("number_sequence")
    op.drop_index(op.f("ix_fiscal_year_organization_id"), table_name="fiscal_year")
    op.drop_table("fiscal_year")
    op.drop_index(op.f("ix_accounting_policy_organization_id"), table_name="accounting_policy")
    op.drop_table("accounting_policy")
    op.drop_index("ix_account_organization_id_level", table_name="account")
    op.drop_index(op.f("ix_account_organization_id"), table_name="account")
    op.drop_table("account")
    # ### end generated schema ###
