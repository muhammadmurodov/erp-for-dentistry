"""a till movement may pay a supplier

The clinic pays most of its suppliers in cash, out of the same drawer it
takes patient money into. Without this column that payment could reach the
ledger and not the till, and the count at the end of the day would be short
by exactly the amount nobody could explain.

The link is held here, on the cash side, for the same reason `payment_id`
already is: `purchasing` need not know that cash sessions exist. Cash sits
above purchasing in the module layering and composes it, not the other way
round.

`uq_cash_movement_supplier_payment` is a partial unique index, matching the
refund index beside it: one drawer movement per supplier payment, so a
repeated request cannot pay the same supplier out of the till twice.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03 18:03:35.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cash_movement", sa.Column("supplier_payment_id", sa.UUID(), nullable=True))
    op.create_index(
        "uq_cash_movement_supplier_payment",
        "cash_movement",
        ["organization_id", "supplier_payment_id"],
        unique=True,
        postgresql_where=sa.text("supplier_payment_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_cash_movement_supplier_payment",
        "cash_movement",
        "supplier_payment",
        ["organization_id", "supplier_payment_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_cash_movement_supplier_payment", "cash_movement", type_="foreignkey")
    op.drop_index(
        "uq_cash_movement_supplier_payment",
        table_name="cash_movement",
        postgresql_where=sa.text("supplier_payment_id IS NOT NULL"),
    )
    op.drop_column("cash_movement", "supplier_payment_id")
