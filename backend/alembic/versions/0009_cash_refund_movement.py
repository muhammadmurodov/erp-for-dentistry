"""a refunded cash payment leaves the drawer

A bug fix, found by probing the settlement paths rather than by a failing
test.

`cash_movement` was unique on `(organization_id, payment_id)`, which stopped a
payment being counted into the drawer twice — the right rule, stated too
broadly. It also made it impossible to record that cash had been paid back
*out* for a payment already receipted, because that row would collide with
the receipt.

So refunding a cash payment reversed the ledger entry and left the drawer
still expecting the money. The session's expected balance stayed at the full
amount while account 5010 went to zero, the end-of-day count reported a
shortage that was not one, and real cash was posted to 9430 as a loss. It is
the same defect as taking cash with no session open, in the other direction,
and it was reachable through the only path that takes cash at all.

The constraint becomes `(organization_id, payment_id, kind)`: one receipt and
one refund per payment, both still impossible to duplicate. Nothing else
changes, and every existing row satisfies the new form unchanged — a payment
with one movement cannot violate a uniqueness rule that is now narrower.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31 18:12:03.117204
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_cash_movement_payment", "cash_movement", type_="unique")
    op.create_unique_constraint(
        "uq_cash_movement_payment_kind",
        "cash_movement",
        ["organization_id", "payment_id", "kind"],
    )


def downgrade() -> None:
    # Going back narrows the rule again. If a refund movement was recorded
    # while this revision was applied, the old constraint cannot be created
    # and the downgrade fails loudly — which is correct: silently dropping
    # the row would lose a record of cash that left the till.
    op.drop_constraint("uq_cash_movement_payment_kind", "cash_movement", type_="unique")
    op.create_unique_constraint(
        "uq_cash_movement_payment",
        "cash_movement",
        ["organization_id", "payment_id"],
    )
