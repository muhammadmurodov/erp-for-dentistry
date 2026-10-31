"""phase3d banking

Revision ID: 0005
Revises: 4b8683485239
Create Date: 2026-10-30 21:40:58.369025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "4b8683485239"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Reconciliation integrity, enforced in the database.
#
# Matching is where the five stages of one economic transaction get tied
# together, so it is also where double-counting would appear. Every rule the
# service applies is restated here, because a direct SQL session, a future
# bulk import or a bug in the service must not be able to produce a bank
# line that claims to be reconciled when it is not.
# ---------------------------------------------------------------------------

#: A statement line is evidence produced by the bank. The business may decide
#: what it means — status, fee, reason — but it may not revise what the bank
#: said. Deleting one is allowed only while nothing is matched to it, which
#: the foreign key's CASCADE and the set-level trigger below together govern.
TRANSACTION_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION bank_transaction_evidence_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (NEW.bank_account_id, NEW.value_date, NEW.direction, NEW.amount,
        NEW.currency, NEW.fingerprint)
       IS DISTINCT FROM
       (OLD.bank_account_id, OLD.value_date, OLD.direction, OLD.amount,
        OLD.currency, OLD.fingerprint)
    THEN
        RAISE EXCEPTION
            'bank transaction % is what the bank reported and cannot be '
            'edited. Correct the mapping and re-import the statement.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_bank_transaction_evidence_immutable
BEFORE UPDATE ON bank_transaction
FOR EACH ROW EXECUTE FUNCTION bank_transaction_evidence_is_immutable();
"""

#: A match row records a fact about a posting that has already been made.
#: Re-pointing it at another record, or changing its amount, would leave the
#: ledger describing one thing and the reconciliation another.
MATCH_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION reconciliation_match_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (NEW.bank_transaction_id, NEW.source_kind, NEW.source_id, NEW.amount,
        NEW.organization_id)
       IS DISTINCT FROM
       (OLD.bank_transaction_id, OLD.source_kind, OLD.source_id, OLD.amount,
        OLD.organization_id)
    THEN
        RAISE EXCEPTION
            'reconciliation match % cannot be re-pointed or re-valued; '
            'remove it and match again', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_reconciliation_match_immutable
BEFORE UPDATE ON reconciliation_match
FOR EACH ROW EXECUTE FUNCTION reconciliation_match_is_immutable();
"""

#: The set-level invariant, and the one that actually prevents inflated
#: turnover: what is matched to a bank line, net of the fee withheld, can
#: never exceed the line, and a line may call itself MATCHED only when the
#: two are equal.
#:
#: Deferred, because a legitimate many-to-one match inserts its rows one at a
#: time and is only coherent at COMMIT. It fires from both tables, so neither
#: adding a match nor editing the line can slip past it.
MATCH_TOTALS = """
CREATE OR REPLACE FUNCTION assert_reconciliation_is_coherent()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_transaction_id uuid;
    v_line           bank_transaction;
    v_matched        numeric(20,4);
    v_accounted      numeric(20,4);
BEGIN
    IF TG_TABLE_NAME = 'bank_transaction' THEN
        v_transaction_id := COALESCE(NEW.id, OLD.id);
    ELSE
        v_transaction_id := COALESCE(NEW.bank_transaction_id,
                                     OLD.bank_transaction_id);
    END IF;

    SELECT * INTO v_line FROM bank_transaction WHERE id = v_transaction_id;
    IF NOT FOUND THEN
        -- The line itself was deleted; its matches went with it.
        RETURN NULL;
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_matched
    FROM reconciliation_match
    WHERE bank_transaction_id = v_transaction_id;

    v_accounted := v_matched - v_line.commission_amount;

    IF v_line.status = 'IGNORED' AND v_matched > 0 THEN
        RAISE EXCEPTION
            'bank transaction % was set aside, so nothing may be matched '
            'to it', v_line.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_accounted > v_line.amount THEN
        RAISE EXCEPTION
            'bank transaction % is %, but % is matched against it '
            '(less % withheld)',
            v_line.id, v_line.amount, v_matched, v_line.commission_amount
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_line.status = 'MATCHED' AND v_accounted <> v_line.amount THEN
        RAISE EXCEPTION
            'bank transaction % is marked reconciled but % of % is '
            'accounted for', v_line.id, v_accounted, v_line.amount
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_line.status <> 'MATCHED' AND v_accounted = v_line.amount
       AND v_matched > 0
    THEN
        RAISE EXCEPTION
            'bank transaction % is fully accounted for but is not marked '
            'reconciled', v_line.id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_reconciliation_match_coherent
AFTER INSERT OR UPDATE OR DELETE ON reconciliation_match
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_reconciliation_is_coherent();

CREATE CONSTRAINT TRIGGER trg_bank_transaction_coherent
AFTER INSERT OR UPDATE ON bank_transaction
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_reconciliation_is_coherent();
"""

DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS trg_bank_transaction_coherent ON bank_transaction;
DROP TRIGGER IF EXISTS trg_reconciliation_match_coherent ON reconciliation_match;
DROP FUNCTION IF EXISTS assert_reconciliation_is_coherent();
DROP TRIGGER IF EXISTS trg_reconciliation_match_immutable ON reconciliation_match;
DROP FUNCTION IF EXISTS reconciliation_match_is_immutable();
DROP TRIGGER IF EXISTS trg_bank_transaction_evidence_immutable ON bank_transaction;
DROP FUNCTION IF EXISTS bank_transaction_evidence_is_immutable();
"""


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "bank_account",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("bank_name", sa.String(length=255), nullable=True),
        sa.Column("mfo", sa.String(length=16), nullable=True),
        sa.Column("account_number", sa.String(length=34), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("column_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "mfo IS NULL OR mfo ~ '^[0-9]{5}$'", name=op.f("ck_bank_account_mfo_is_five_digits")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["account.organization_id", "account.id"],
            name="fk_bank_account_ledger_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_bank_account_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_account")),
        sa.UniqueConstraint("organization_id", "code", name="uq_bank_account_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_bank_account_organization_id_id"),
    )
    op.create_index(
        op.f("ix_bank_account_organization_id"), "bank_account", ["organization_id"], unique=False
    )
    op.create_table(
        "bank_statement_import",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("bank_account_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("COMPLETED", "FAILED", name="bank_import_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False),
        sa.Column("duplicates_skipped", sa.Integer(), nullable=False),
        sa.Column("rows_rejected", sa.Integer(), nullable=False),
        sa.Column("problems", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("imported_by_user_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_bank_statement_import_imported_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "bank_account_id"],
            ["bank_account.organization_id", "bank_account.id"],
            name="fk_bank_statement_import_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_bank_statement_import_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_statement_import")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_bank_statement_import_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_bank_statement_import_organization_id"),
        "bank_statement_import",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "bank_transaction",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("bank_account_id", sa.UUID(), nullable=False),
        sa.Column("import_id", sa.UUID(), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("IN", "OUT", name="bank_transaction_direction", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("counterparty_name", sa.String(length=255), nullable=True),
        sa.Column("counterparty_account", sa.String(length=34), nullable=True),
        sa.Column("counterparty_mfo", sa.String(length=16), nullable=True),
        sa.Column("document_number", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "UNMATCHED",
                "MATCHED",
                "IGNORED",
                name="bank_transaction_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("ignored_reason", sa.Text(), nullable=True),
        sa.Column(
            "commission_amount",
            sa.Numeric(precision=20, scale=4),
            server_default="0",
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "status <> 'IGNORED' OR commission_amount = 0",
            name=op.f("ck_bank_transaction_ignored_line_has_no_commission"),
        ),
        sa.CheckConstraint(
            "status <> 'IGNORED' OR ignored_reason IS NOT NULL",
            name=op.f("ck_bank_transaction_ignoring_needs_a_reason"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_bank_transaction_amount_positive")),
        sa.CheckConstraint(
            "commission_amount >= 0", name=op.f("ck_bank_transaction_commission_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "bank_account_id"],
            ["bank_account.organization_id", "bank_account.id"],
            name="fk_bank_transaction_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["bank_statement_import.organization_id", "bank_statement_import.id"],
            name="fk_bank_transaction_import",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_bank_transaction_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_transaction")),
        sa.UniqueConstraint(
            "organization_id",
            "bank_account_id",
            "fingerprint",
            name="uq_bank_transaction_fingerprint",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_bank_transaction_organization_id_id"),
    )
    op.create_index(
        op.f("ix_bank_transaction_organization_id"),
        "bank_transaction",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_bank_transaction_queue",
        "bank_transaction",
        ["organization_id", "bank_account_id", "status", "value_date"],
        unique=False,
    )
    op.create_table(
        "reconciliation_match",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("bank_transaction_id", sa.UUID(), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum(
                "CASH_DEPOSIT",
                "CARD_PAYMENT",
                "BANK_PAYMENT",
                name="matchable_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("matched_by_user_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint("amount > 0", name=op.f("ck_reconciliation_match_amount_positive")),
        sa.ForeignKeyConstraint(
            ["matched_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_reconciliation_match_matched_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "bank_transaction_id"],
            ["bank_transaction.organization_id", "bank_transaction.id"],
            name="fk_reconciliation_match_transaction",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_reconciliation_match_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_match")),
        sa.UniqueConstraint(
            "organization_id",
            "bank_transaction_id",
            "source_kind",
            "source_id",
            name="uq_reconciliation_match_pair",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_reconciliation_match_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_reconciliation_match_organization_id"),
        "reconciliation_match",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_reconciliation_match_source",
        "reconciliation_match",
        ["organization_id", "source_kind", "source_id"],
        unique=False,
    )
    # ### end Alembic commands ###

    op.execute(TRANSACTION_IMMUTABILITY)
    op.execute(MATCH_IMMUTABILITY)
    op.execute(MATCH_TOTALS)


def downgrade() -> None:
    op.execute(DROP_TRIGGERS)
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_reconciliation_match_source", table_name="reconciliation_match")
    op.drop_index(
        op.f("ix_reconciliation_match_organization_id"), table_name="reconciliation_match"
    )
    op.drop_table("reconciliation_match")
    op.drop_index("ix_bank_transaction_queue", table_name="bank_transaction")
    op.drop_index(op.f("ix_bank_transaction_organization_id"), table_name="bank_transaction")
    op.drop_table("bank_transaction")
    op.drop_index(
        op.f("ix_bank_statement_import_organization_id"), table_name="bank_statement_import"
    )
    op.drop_table("bank_statement_import")
    op.drop_index(op.f("ix_bank_account_organization_id"), table_name="bank_account")
    op.drop_table("bank_account")
    # ### end Alembic commands ###
