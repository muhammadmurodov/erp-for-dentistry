"""settlement requests and the database-level overpayment guard

Two things, both about a payment form being submitted twice.

**`settlement_request`** remembers that one submission of the payment form
was already carried out. It holds the amounts as submitted and the ids of the
payments it produced — no balance, nothing anything reads a figure back from.
Its unique `(organization_id, request_key)` is what makes a double-click, a
retried request or a refreshed page settle an invoice once instead of twice.

**The over-allocation guard is rewritten**, and this part is a bug fix.

Migration 0003 created `assert_allocation_within_limits` as a DEFERRABLE
constraint trigger that sums every allocation row against the payment and the
invoice. Migration 0006 then made an allocation reversible — reversed, not
deleted — without teaching the guard about it. The result is a trigger that
counts money which has been taken back off: re-applying a payment to the same
invoice after a reversal, which 0006 deliberately allows, would be refused at
COMMIT as an over-allocation. The tests did not catch it because they run
inside a transaction that is rolled back, and a deferred trigger never fires
without a commit.

The replacement counts live rows only, scopes both sums by organization, and
takes the invoice's and the payment's row locks — invoice first, then payment,
the same order `BillingService.allocate` takes them — so two interleaved
settlements queue instead of each reading a stale balance. It also stops being
deferred: raising at the statement that caused it names the row that was
wrong, and nothing in this system has a legitimate reason to be transiently
over-allocated mid-transaction.

The service's own check stays. It is the one that produces a sentence a
cashier can read; this is the one that holds regardless of which code path, or
which hand-written statement, reaches the table.

The downgrade restores the 0003 function and its deferred trigger, reversal
bug included, and drops `settlement_request` — which discards the replay
protection for submissions already made, so a request retried across the
downgrade could be applied a second time. Development only.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 16:35:47.792956
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Spans sibling rows, so no CHECK can express it. Three things the 0003
#: version did not do: ignore reversed allocations, scope the sums by
#: organization, and take the row locks that make the check hold under
#: concurrency. The message wording is kept so that callers matching on
#: "over-allocated" keep working.
ALLOCATION_GUARD = """
CREATE OR REPLACE FUNCTION assert_allocation_within_limits()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_org          uuid;
    v_payment_id   uuid;
    v_invoice_id   uuid;
    v_allocated    numeric(20,4);
    v_limit        numeric(20,4);
    v_label        text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_org        := OLD.organization_id;
        v_payment_id := OLD.payment_id;
        v_invoice_id := OLD.invoice_id;
    ELSE
        v_org        := NEW.organization_id;
        v_payment_id := NEW.payment_id;
        v_invoice_id := NEW.invoice_id;
    END IF;

    -- Invoice first, then payment: the order `BillingService.allocate` takes
    -- them, so a concurrent settlement waits rather than deadlocking.
    SELECT total, number INTO v_limit, v_label
      FROM invoice
     WHERE organization_id = v_org AND id = v_invoice_id
       FOR UPDATE;

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
      FROM payment_allocation
     WHERE organization_id = v_org
       AND invoice_id = v_invoice_id
       AND reversed_at IS NULL;

    IF v_limit IS NOT NULL AND v_allocated > v_limit THEN
        RAISE EXCEPTION
            'invoice % is over-allocated: % applied against a total of %',
            v_label, v_allocated, v_limit
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT amount, number INTO v_limit, v_label
      FROM payment
     WHERE organization_id = v_org AND id = v_payment_id
       FOR UPDATE;

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
      FROM payment_allocation
     WHERE organization_id = v_org
       AND payment_id = v_payment_id
       AND reversed_at IS NULL;

    IF v_limit IS NOT NULL AND v_allocated > v_limit THEN
        RAISE EXCEPTION
            'payment % is over-allocated: % applied against a payment of %',
            v_label, v_allocated, v_limit
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_payment_allocation_within_limits ON payment_allocation;

CREATE TRIGGER trg_payment_allocation_within_limits
    AFTER INSERT OR UPDATE OR DELETE ON payment_allocation
    FOR EACH ROW EXECUTE FUNCTION assert_allocation_within_limits();
"""

#: The 0003 definition, restored verbatim by the downgrade so that moving
#: back and forward over this revision is symmetrical. The reversal bug comes
#: back with it, which is the honest consequence of going back.
ALLOCATION_GUARD_0003 = """
CREATE OR REPLACE FUNCTION assert_allocation_within_limits()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_payment_id   uuid;
    v_invoice_id   uuid;
    v_allocated    numeric(20,4);
    v_limit        numeric(20,4);
    v_label        text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_payment_id := OLD.payment_id;
        v_invoice_id := OLD.invoice_id;
    ELSE
        v_payment_id := NEW.payment_id;
        v_invoice_id := NEW.invoice_id;
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
    FROM payment_allocation WHERE payment_id = v_payment_id;
    SELECT amount, number INTO v_limit, v_label
    FROM payment WHERE id = v_payment_id;

    IF v_limit IS NOT NULL AND v_allocated > v_limit THEN
        RAISE EXCEPTION
            'payment % is over-allocated: % applied against a payment of %',
            v_label, v_allocated, v_limit
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
    FROM payment_allocation WHERE invoice_id = v_invoice_id;
    SELECT total, number INTO v_limit, v_label
    FROM invoice WHERE id = v_invoice_id;

    IF v_limit IS NOT NULL AND v_allocated > v_limit THEN
        RAISE EXCEPTION
            'invoice % is over-allocated: % applied against a total of %',
            v_label, v_allocated, v_limit
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_payment_allocation_within_limits ON payment_allocation;

CREATE CONSTRAINT TRIGGER trg_payment_allocation_within_limits
AFTER INSERT OR UPDATE OR DELETE ON payment_allocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_allocation_within_limits();
"""


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "settlement_request",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("requested_cash", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("requested_card", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("requested_advances", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("cash_payment_id", sa.UUID(), nullable=True),
        sa.Column("card_payment_id", sa.UUID(), nullable=True),
        sa.Column("applied_advances", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("remaining_after", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
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
            "requested_advances >= 0", name=op.f("ck_settlement_request_advances_not_negative")
        ),
        sa.CheckConstraint(
            "requested_card >= 0", name=op.f("ck_settlement_request_card_not_negative")
        ),
        sa.CheckConstraint(
            "requested_cash + requested_card + requested_advances > 0",
            name=op.f("ck_settlement_request_settlement_is_not_empty"),
        ),
        sa.CheckConstraint(
            "requested_cash >= 0", name=op.f("ck_settlement_request_cash_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoice.organization_id", "invoice.id"],
            name="fk_settlement_request_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_settlement_request_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_settlement_request_submitted_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_request")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_settlement_request_organization_id_id"
        ),
        sa.UniqueConstraint("organization_id", "request_key", name="uq_settlement_request_key"),
    )
    op.create_index(
        "ix_settlement_request_invoice",
        "settlement_request",
        ["organization_id", "invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_settlement_request_organization_id"),
        "settlement_request",
        ["organization_id"],
        unique=False,
    )
    # ### end Alembic commands ###
    op.execute(ALLOCATION_GUARD)


def downgrade() -> None:
    op.execute(ALLOCATION_GUARD_0003)
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_settlement_request_organization_id"), table_name="settlement_request")
    op.drop_index("ix_settlement_request_invoice", table_name="settlement_request")
    op.drop_table("settlement_request")
    # ### end Alembic commands ###
