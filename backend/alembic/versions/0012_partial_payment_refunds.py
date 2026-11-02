"""part of a payment can be given back

Refunding a payment in full was the only refund the system could record. The
clinic's commonest refund is not that: the cashier takes 600 000 for a bill
of 400 000 and the patient wants the spare 200 000 back. There was no way to
record it, so it either did not happen or it happened off the books.

A partial refund cannot be a reversal. There is nothing to reverse partially,
the payment stays live, and a second partial refund must be able to follow
the first. So each refund becomes a record in `payment_refund`, and what is
left refundable is derived by subtracting them from the unapplied part of the
payment — the same shape as an invoice's balance (ADR-026).

`cash_movement` needs one change to allow it. Its uniqueness rule,
`(organization_id, payment_id, kind)`, says one receipt and one refund per
payment; that is right for a full refund and wrong for partial ones. It
becomes a *partial* index over whole-payment movements only, and a second
unique index guarantees one drawer movement per refund record — so the
protection ADR-040 was written for is kept in both cases: a refund cannot be
paid out of the till twice.

Nothing is dropped and no existing row changes. Every cash movement already
recorded has `payment_refund_id IS NULL` and so falls under the same rule it
was written under.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01 19:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_refund",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("refunded_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("refunded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("self_approved", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_refund")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_payment_refund_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["refunded_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_payment_refund_refunded_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["payment.organization_id", "payment.id"],
            name="fk_payment_refund_payment",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id", "number", name="uq_payment_refund_organization_id_number"
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_payment_refund_organization_id_id")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payment_refund_amount_positive")),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f("ck_payment_refund_reason_not_blank")
        ),
    )
    op.create_index(
        op.f("ix_payment_refund_organization_id"), "payment_refund", ["organization_id"]
    )
    op.create_index(
        "ix_payment_refund_payment", "payment_refund", ["organization_id", "payment_id"]
    )
    op.create_index(
        "ix_payment_refund_day", "payment_refund", ["organization_id", "refunded_on"]
    )

    op.add_column(
        "cash_movement",
        sa.Column("payment_refund_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cash_movement_payment_refund",
        "cash_movement",
        "payment_refund",
        ["organization_id", "payment_refund_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    # The rule narrows to whole-payment movements. Partial refunds are
    # excluded here and guarded by the index below instead.
    op.drop_constraint("uq_cash_movement_payment_kind", "cash_movement", type_="unique")
    op.create_index(
        "uq_cash_movement_payment_kind",
        "cash_movement",
        ["organization_id", "payment_id", "kind"],
        unique=True,
        postgresql_where=sa.text("payment_refund_id IS NULL"),
    )
    op.create_index(
        "uq_cash_movement_refund",
        "cash_movement",
        ["organization_id", "payment_refund_id"],
        unique=True,
        postgresql_where=sa.text("payment_refund_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Going back removes the ability to record a partial refund. If one was
    # recorded while this revision was applied, dropping the table would
    # destroy the only record that money left the till, and re-creating the
    # old constraint would fail anyway — so the downgrade fails loudly rather
    # than deleting anything (ADR-043).
    op.drop_index("uq_cash_movement_refund", table_name="cash_movement")
    op.drop_index("uq_cash_movement_payment_kind", table_name="cash_movement")
    op.create_unique_constraint(
        "uq_cash_movement_payment_kind",
        "cash_movement",
        ["organization_id", "payment_id", "kind"],
    )
    op.drop_constraint("fk_cash_movement_payment_refund", "cash_movement", type_="foreignkey")
    op.drop_column("cash_movement", "payment_refund_id")
    op.drop_index("ix_payment_refund_day", table_name="payment_refund")
    op.drop_index("ix_payment_refund_payment", table_name="payment_refund")
    op.drop_index(op.f("ix_payment_refund_organization_id"), table_name="payment_refund")
    op.drop_table("payment_refund")
