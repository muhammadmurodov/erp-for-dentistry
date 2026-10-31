"""phase 3 sales and receivables

Revision ID: b03dde74659e
Revises: 738cbc2d61cf
Create Date: 2026-10-30 20:24:57.215459
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b03dde74659e"
down_revision: str | None = "738cbc2d61cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Receivable integrity, enforced in the database.
#
# The application checks the same rules under a row lock and produces better
# messages. These exist so the rules hold even when something bypasses the
# service — and because over-allocation is the specification's named
# concurrency hazard: two cashiers applying the same payment at once.
# ---------------------------------------------------------------------------

#: An invoice can never have more allocated against it than it is worth, and
#: a payment can never have more allocated out of it than was received.
#:
#: A sum across sibling rows cannot be a CHECK, so this is a DEFERRABLE
#: constraint trigger: it runs at COMMIT, after any set of allocations in the
#: transaction is complete.
ALLOCATION_GUARD = """
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

CREATE CONSTRAINT TRIGGER trg_payment_allocation_within_limits
AFTER INSERT OR UPDATE OR DELETE ON payment_allocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_allocation_within_limits();
"""

#: An issued invoice's figures are what the ledger was told. Editing them
#: afterwards would make the receivable disagree with the posting that
#: created it, so the same reverse-and-replace rule applies as to a journal
#: entry: void it and raise a new one.
INVOICE_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION invoice_amounts_are_immutable_once_issued()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'DRAFT' THEN
        RETURN NEW;
    END IF;

    IF (NEW.subtotal, NEW.tax_total, NEW.total, NEW.currency,
        NEW.customer_id, NEW.issue_date, NEW.number)
       IS DISTINCT FROM
       (OLD.subtotal, OLD.tax_total, OLD.total, OLD.currency,
        OLD.customer_id, OLD.issue_date, OLD.number)
    THEN
        RAISE EXCEPTION
            'invoice % has been issued; its amounts cannot be changed. '
            'Void it and raise a new one.',
            OLD.number
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_invoice_amounts_immutable
BEFORE UPDATE ON invoice
FOR EACH ROW EXECUTE FUNCTION invoice_amounts_are_immutable_once_issued();

CREATE OR REPLACE FUNCTION invoice_line_is_immutable_once_issued()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
    v_number text;
BEGIN
    SELECT status, number INTO v_status, v_number
    FROM invoice
    WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;

    IF v_status IS NULL OR v_status = 'DRAFT' THEN
        RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    RAISE EXCEPTION
        'invoice % has been issued; its lines cannot be %',
        v_number, CASE TG_OP WHEN 'DELETE' THEN 'deleted' ELSE 'edited' END
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER trg_invoice_line_immutable
BEFORE UPDATE OR DELETE ON invoice_line
FOR EACH ROW EXECUTE FUNCTION invoice_line_is_immutable_once_issued();
"""

#: A payment's amount is what was posted to the ledger. Changing it would
#: make the cash account disagree with the money that actually arrived.
PAYMENT_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION payment_amount_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (NEW.amount, NEW.currency, NEW.customer_id, NEW.received_on, NEW.number)
       IS DISTINCT FROM
       (OLD.amount, OLD.currency, OLD.customer_id, OLD.received_on, OLD.number)
    THEN
        RAISE EXCEPTION
            'payment % has been posted; its amount cannot be changed. '
            'Refund it and record a new one.',
            OLD.number
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_payment_amount_immutable
BEFORE UPDATE ON payment
FOR EACH ROW EXECUTE FUNCTION payment_amount_is_immutable();
"""

SALES_GUARDS = (ALLOCATION_GUARD, INVOICE_IMMUTABILITY, PAYMENT_IMMUTABILITY)

DROP_SALES_GUARDS = """
DROP TRIGGER IF EXISTS trg_payment_amount_immutable ON payment;
DROP TRIGGER IF EXISTS trg_invoice_line_immutable ON invoice_line;
DROP TRIGGER IF EXISTS trg_invoice_amounts_immutable ON invoice;
DROP TRIGGER IF EXISTS trg_payment_allocation_within_limits ON payment_allocation;
DROP FUNCTION IF EXISTS payment_amount_is_immutable();
DROP FUNCTION IF EXISTS invoice_line_is_immutable_once_issued();
DROP FUNCTION IF EXISTS invoice_amounts_are_immutable_once_issued();
DROP FUNCTION IF EXISTS assert_allocation_within_limits();
"""


def upgrade() -> None:
    # ### schema (generated from the SQLAlchemy models) ###
    op.create_table(
        "customer",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("PERSON", "COMPANY", name="customer_kind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("tax_number", sa.String(length=20), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "tax_number IS NULL OR tax_number ~ '^[0-9]{9,14}$'",
            name=op.f("ck_customer_tax_number_digits"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_customer_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer")),
        sa.UniqueConstraint("organization_id", "code", name="uq_customer_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_customer_organization_id_id"),
    )
    op.create_index(
        "ix_customer_name", "customer", ["organization_id", "display_name"], unique=False
    )
    op.create_index(
        op.f("ix_customer_organization_id"), "customer", ["organization_id"], unique=False
    )
    op.create_table(
        "service",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_uz", sa.String(length=255), nullable=True),
        sa.Column("name_ru", sa.String(length=255), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "TREATMENT",
                "DIAGNOSTIC",
                "CONSULTATION",
                "MATERIAL",
                "OTHER",
                name="service_category",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("mxik_code", sa.String(length=32), nullable=True),
        sa.Column("unit_code", sa.String(length=16), nullable=True),
        sa.Column("unit_label", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
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
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_service_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service")),
        sa.UniqueConstraint("organization_id", "code", name="uq_service_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_service_organization_id_id"),
    )
    op.create_index("ix_service_active", "service", ["organization_id", "is_active"], unique=False)
    op.create_index(
        op.f("ix_service_organization_id"), "service", ["organization_id"], unique=False
    )
    op.create_table(
        "invoice",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "ISSUED",
                "PARTIALLY_PAID",
                "PAID",
                "CANCELLED",
                "VOID",
                name="invoice_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_treatment", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by_user_id", sa.UUID(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
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
            "status <> 'ISSUED' OR issued_at IS NOT NULL",
            name=op.f("ck_invoice_issued_has_timestamp"),
        ),
        sa.CheckConstraint(
            "due_date IS NULL OR due_date >= issue_date",
            name=op.f("ck_invoice_due_not_before_issue"),
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND tax_total >= 0 AND total >= 0",
            name=op.f("ck_invoice_amounts_not_negative"),
        ),
        sa.CheckConstraint(
            "total = subtotal + tax_total", name=op.f("ck_invoice_total_is_subtotal_plus_tax")
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_invoice_issued_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customer.organization_id", "customer.id"],
            name="fk_invoice_customer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_invoice_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice")),
        sa.UniqueConstraint("organization_id", "id", name="uq_invoice_organization_id_id"),
        sa.UniqueConstraint("organization_id", "number", name="uq_invoice_organization_id_number"),
    )
    op.create_index(
        "ix_invoice_customer", "invoice", ["organization_id", "customer_id", "status"], unique=False
    )
    op.create_index(
        "ix_invoice_issue_date", "invoice", ["organization_id", "issue_date"], unique=False
    )
    op.create_index(
        op.f("ix_invoice_organization_id"), "invoice", ["organization_id"], unique=False
    )
    op.create_table(
        "payment",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column(
            "method",
            sa.Enum(
                "CASH", "CARD", "BANK_TRANSFER", name="payment_method", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("RECORDED", "REFUNDED", name="payment_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint("amount > 0", name=op.f("ck_payment_amount_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customer.organization_id", "customer.id"],
            name="fk_payment_customer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_payment_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_payment_recorded_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment")),
        sa.UniqueConstraint("organization_id", "id", name="uq_payment_organization_id_id"),
        sa.UniqueConstraint("organization_id", "number", name="uq_payment_organization_id_number"),
    )
    op.create_index(
        "ix_payment_customer", "payment", ["organization_id", "customer_id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_organization_id"), "payment", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_payment_received_on", "payment", ["organization_id", "received_on"], unique=False
    )
    op.create_table(
        "service_price",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
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
        sa.CheckConstraint("amount >= 0", name=op.f("ck_service_price_amount_not_negative")),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_service_price_effective_range_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "service_id"],
            ["service.organization_id", "service.id"],
            name="fk_service_price_service",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_service_price_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_price")),
        sa.UniqueConstraint("organization_id", "id", name="uq_service_price_organization_id_id"),
    )
    op.create_index(
        "ix_service_price_lookup",
        "service_price",
        ["organization_id", "service_id", "effective_from"],
        unique=False,
    )
    op.create_index(
        op.f("ix_service_price_organization_id"), "service_price", ["organization_id"], unique=False
    )
    op.create_table(
        "invoice_line",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("mxik_code", sa.String(length=32), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("line_number >= 1", name=op.f("ck_invoice_line_line_number_positive")),
        sa.CheckConstraint("line_total >= 0", name=op.f("ck_invoice_line_line_total_not_negative")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_invoice_line_quantity_positive")),
        sa.CheckConstraint("unit_price >= 0", name=op.f("ck_invoice_line_unit_price_not_negative")),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoice.organization_id", "invoice.id"],
            name="fk_invoice_line_invoice",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "service_id"],
            ["service.organization_id", "service.id"],
            name="fk_invoice_line_service",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_invoice_line_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_line")),
        sa.UniqueConstraint("organization_id", "id", name="uq_invoice_line_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "invoice_id", "line_number", name="uq_invoice_line_number"
        ),
    )
    op.create_index(
        op.f("ix_invoice_line_organization_id"), "invoice_line", ["organization_id"], unique=False
    )
    op.create_table(
        "payment_allocation",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("allocated_by_user_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint("amount > 0", name=op.f("ck_payment_allocation_amount_positive")),
        sa.ForeignKeyConstraint(
            ["allocated_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_payment_allocation_allocated_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoice.organization_id", "invoice.id"],
            name="fk_payment_allocation_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["payment.organization_id", "payment.id"],
            name="fk_payment_allocation_payment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_payment_allocation_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_allocation")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_payment_allocation_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "payment_id",
            "invoice_id",
            name="uq_payment_allocation_payment_invoice",
        ),
    )
    op.create_index(
        "ix_payment_allocation_invoice",
        "payment_allocation",
        ["organization_id", "invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_allocation_organization_id"),
        "payment_allocation",
        ["organization_id"],
        unique=False,
    )
    # ### end generated schema ###

    for statement in SALES_GUARDS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(DROP_SALES_GUARDS)

    # ### schema (generated from the SQLAlchemy models) ###
    op.drop_index(op.f("ix_payment_allocation_organization_id"), table_name="payment_allocation")
    op.drop_index("ix_payment_allocation_invoice", table_name="payment_allocation")
    op.drop_table("payment_allocation")
    op.drop_index(op.f("ix_invoice_line_organization_id"), table_name="invoice_line")
    op.drop_table("invoice_line")
    op.drop_index(op.f("ix_service_price_organization_id"), table_name="service_price")
    op.drop_index("ix_service_price_lookup", table_name="service_price")
    op.drop_table("service_price")
    op.drop_index("ix_payment_received_on", table_name="payment")
    op.drop_index(op.f("ix_payment_organization_id"), table_name="payment")
    op.drop_index("ix_payment_customer", table_name="payment")
    op.drop_table("payment")
    op.drop_index(op.f("ix_invoice_organization_id"), table_name="invoice")
    op.drop_index("ix_invoice_issue_date", table_name="invoice")
    op.drop_index("ix_invoice_customer", table_name="invoice")
    op.drop_table("invoice")
    op.drop_index(op.f("ix_service_organization_id"), table_name="service")
    op.drop_index("ix_service_active", table_name="service")
    op.drop_table("service")
    op.drop_index(op.f("ix_customer_organization_id"), table_name="customer")
    op.drop_index("ix_customer_name", table_name="customer")
    op.drop_table("customer")
    # ### end generated schema ###
