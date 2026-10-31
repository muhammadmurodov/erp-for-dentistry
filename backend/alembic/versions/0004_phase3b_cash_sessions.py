"""phase 3b cash sessions

Revision ID: 4b8683485239
Revises: b03dde74659e
Create Date: 2026-10-30 21:04:32.226538
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4b8683485239"
down_revision: str | None = "b03dde74659e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Cash integrity, enforced in the database.
# ---------------------------------------------------------------------------

#: One open session per register. Two would make "expected cash" meaningless,
#: and no application check survives two cashiers opening a till at the same
#: instant. A partial unique index does.
ONE_OPEN_SESSION = """
CREATE UNIQUE INDEX uq_cash_session_one_open_per_register
ON cash_session (organization_id, register_id)
WHERE status = 'OPEN';
"""

#: A closed session is a reconciled record. Its count, its discrepancy and
#: its movements are what the ledger was told, so they cannot be revised
#: afterwards — the same reverse-and-replace rule as a journal entry.
CLOSED_SESSION_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION cash_session_is_immutable_once_closed()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status <> 'CLOSED' THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'cash session % is closed and cannot be changed', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER trg_cash_session_immutable
BEFORE UPDATE OR DELETE ON cash_session
FOR EACH ROW EXECUTE FUNCTION cash_session_is_immutable_once_closed();

CREATE OR REPLACE FUNCTION cash_movement_follows_session_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status
    FROM cash_session
    WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.session_id ELSE NEW.session_id END;

    -- The session is being removed in the same statement.
    IF v_status IS NULL THEN
        RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    IF TG_OP = 'INSERT' AND v_status <> 'OPEN' THEN
        RAISE EXCEPTION
            'cash session % is % and cannot take further movements',
            NEW.session_id, lower(v_status)
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP IN ('UPDATE', 'DELETE') AND v_status = 'CLOSED' THEN
        RAISE EXCEPTION
            'cash session % is closed; its movements cannot be %',
            COALESCE(NEW.session_id, OLD.session_id),
            CASE TG_OP WHEN 'DELETE' THEN 'deleted' ELSE 'edited' END
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_cash_movement_follows_session
BEFORE INSERT OR UPDATE OR DELETE ON cash_movement
FOR EACH ROW EXECUTE FUNCTION cash_movement_follows_session_state();
"""

CASH_GUARDS = (ONE_OPEN_SESSION, CLOSED_SESSION_IMMUTABILITY)

DROP_CASH_GUARDS = """
DROP TRIGGER IF EXISTS trg_cash_movement_follows_session ON cash_movement;
DROP TRIGGER IF EXISTS trg_cash_session_immutable ON cash_session;
DROP FUNCTION IF EXISTS cash_movement_follows_session_state();
DROP FUNCTION IF EXISTS cash_session_is_immutable_once_closed();
DROP INDEX IF EXISTS uq_cash_session_one_open_per_register;
"""


def upgrade() -> None:
    # ### schema (generated from the SQLAlchemy models) ###
    op.create_table(
        "cash_register",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            ["organization_id", "account_id"],
            ["account.organization_id", "account.id"],
            name="fk_cash_register_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_cash_register_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_register")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_cash_register_organization_id_code"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_cash_register_organization_id_id"),
    )
    op.create_index(
        op.f("ix_cash_register_organization_id"), "cash_register", ["organization_id"], unique=False
    )
    op.create_table(
        "cash_session",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("register_id", sa.UUID(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "COUNTED",
                "CLOSED",
                name="cash_session_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_by_user_id", sa.UUID(), nullable=True),
        sa.Column("counted_amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("discrepancy", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("discrepancy_reason", sa.Text(), nullable=True),
        sa.Column("approved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status <> 'CLOSED' OR closed_at IS NOT NULL",
            name=op.f("ck_cash_session_closed_has_timestamp"),
        ),
        sa.CheckConstraint(
            "counted_amount IS NOT NULL OR discrepancy IS NULL",
            name=op.f("ck_cash_session_discrepancy_needs_a_count"),
        ),
        sa.CheckConstraint(
            "counted_amount IS NULL OR counted_amount >= 0",
            name=op.f("ck_cash_session_counted_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_cash_session_approved_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["counted_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_cash_session_counted_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_cash_session_opened_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "register_id"],
            ["cash_register.organization_id", "cash_register.id"],
            name="fk_cash_session_register",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_cash_session_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_session")),
        sa.UniqueConstraint("organization_id", "id", name="uq_cash_session_organization_id_id"),
    )
    op.create_index(
        "ix_cash_session_date", "cash_session", ["organization_id", "business_date"], unique=False
    )
    op.create_index(
        op.f("ix_cash_session_organization_id"), "cash_session", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_cash_session_register",
        "cash_session",
        ["organization_id", "register_id", "status"],
        unique=False,
    )
    op.create_table(
        "cash_movement",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "OPENING_FLOAT",
                "RECEIPT",
                "REFUND",
                "EXPENSE",
                "DEPOSIT",
                "ADJUSTMENT_IN",
                "ADJUSTMENT_OUT",
                name="cash_movement_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=True),
        sa.Column(
            "deposit_route",
            sa.Enum("STAFF", "COLLECTION", name="cash_deposit_route", native_enum=False, length=32),
            nullable=True,
        ),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=True),
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
            "kind <> 'DEPOSIT' OR deposit_route IS NOT NULL",
            name=op.f("ck_cash_movement_deposit_needs_a_route"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_cash_movement_amount_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["payment.organization_id", "payment.id"],
            name="fk_cash_movement_payment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id"],
            ["cash_session.organization_id", "cash_session.id"],
            name="fk_cash_movement_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_cash_movement_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_cash_movement_recorded_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_movement")),
        sa.UniqueConstraint("organization_id", "id", name="uq_cash_movement_organization_id_id"),
        sa.UniqueConstraint("organization_id", "payment_id", name="uq_cash_movement_payment"),
    )
    op.create_index(
        op.f("ix_cash_movement_organization_id"), "cash_movement", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_cash_movement_session",
        "cash_movement",
        ["organization_id", "session_id", "kind"],
        unique=False,
    )
    # ### end generated schema ###

    for statement in CASH_GUARDS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(DROP_CASH_GUARDS)

    # ### schema (generated from the SQLAlchemy models) ###
    op.drop_index("ix_cash_movement_session", table_name="cash_movement")
    op.drop_index(op.f("ix_cash_movement_organization_id"), table_name="cash_movement")
    op.drop_table("cash_movement")
    op.drop_index("ix_cash_session_register", table_name="cash_session")
    op.drop_index(op.f("ix_cash_session_organization_id"), table_name="cash_session")
    op.drop_index("ix_cash_session_date", table_name="cash_session")
    op.drop_table("cash_session")
    op.drop_index(op.f("ix_cash_register_organization_id"), table_name="cash_register")
    op.drop_table("cash_register")
    # ### end generated schema ###
