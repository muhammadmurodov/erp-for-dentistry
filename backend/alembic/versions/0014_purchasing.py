"""purchasing: suppliers, requests, orders, receipts, bills and payments

Phase 4, first half. Nine tables, and their shape is the whole decision:
**ordering, receiving, invoicing and paying are four records, not four flags
on one.** A single `purchase` row with booleans cannot answer the questions a
clinic actually asks — what did we order that has not arrived, what has
arrived that we have not been billed for, what are we billed for that we have
not paid — and those are the questions that catch a supplier invoicing the
same delivery twice.

What posts, and what deliberately does not:

* A **purchase order posts nothing.** It is a promise to buy. No asset has
  arrived and no liability exists until one does; posting at order time is
  how a cancelled order leaves a phantom payable behind.
* A **goods receipt posts nothing either**, and that is a decision rather
  than an omission. The textbook accrual debits stock and credits a
  goods-received-not-invoiced account. The clinic's workbook has no such
  account: its rule posts materials straight to 1010 against payable 6010
  when the invoice arrives. Inventing a GRNI account would be inventing a
  statutory classification, which this project does not do. The receipt
  records what arrived; the bill does the posting; the gap between them is
  recorded as an open question.
* **Registering a supplier invoice posts**, under the policy, and the bill
  becomes immutable.
* **Paying posts**, and what is left on a bill is derived from live
  allocations — never a stored `paid` column (ADR-026).

A supplier payment carries **no invoice**: money handed over is money handed
over, and which bills it settles is a separate decision in
`supplier_payment_allocation`. The unallocated part is an advance, which is
how the clinic actually buys — it pays first and collects later. The same
shape as a patient's unapplied payment, for the same reason.

Constraints worth naming:

* `uq_supplier_invoice_supplier_reference` — one supplier's own invoice
  number may appear once. This is what stops a delivery being paid for twice.
* `ck_supplier_invoice_void_has_reason` and
  `ck_supplier_invoice_registered_has_time` — a bill's status cannot
  contradict its own dates and reasons.
* `ck_goods_receipt_line_rejection_has_reason` — goods turned away on the
  doorstep say why.
* Every child table carries a **composite** foreign key on
  `(organization_id, parent_id)`, so a line belonging to another tenant's
  order is unrepresentable rather than merely unlikely.

`purchase_order.raised_by_user_id` is stored rather than read back out of the
audit trail: the segregation-of-duties check needs it on every approval, and a
control that depends on querying the audit log is one that breaks the day the
audit log is archived.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03 17:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_request",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "APPROVED",
                "REJECTED",
                "ORDERED",
                "CANCELLED",
                name="purchase_request_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_purchase_request_decided_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_purchase_request_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_purchase_request_requested_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_request")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_purchase_request_organization_id_code"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_purchase_request_organization_id_id"),
    )
    op.create_index(
        op.f("ix_purchase_request_organization_id"),
        "purchase_request",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_request_status",
        "purchase_request",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_table(
        "supplier",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tax_number", sa.String(length=32), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_supplier_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier")),
        sa.UniqueConstraint("organization_id", "code", name="uq_supplier_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_supplier_organization_id_id"),
    )
    op.create_index("ix_supplier_name", "supplier", ["organization_id", "name"], unique=False)
    op.create_index(
        op.f("ix_supplier_organization_id"), "supplier", ["organization_id"], unique=False
    )
    op.create_table(
        "purchase_order",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "APPROVED",
                "SENT",
                "PARTIALLY_RECEIVED",
                "RECEIVED",
                "CANCELLED",
                "CLOSED",
                name="purchase_order_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("ordered_on", sa.Date(), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("raised_by_user_id", sa.UUID(), nullable=True),
        sa.Column("approved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("self_approved", sa.Boolean(), nullable=False),
        sa.Column("closed_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint("total >= 0", name="ck_purchase_order_total"),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_purchase_order_approved_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "request_id"],
            ["purchase_request.organization_id", "purchase_request.id"],
            name="fk_purchase_order_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_id"],
            ["supplier.organization_id", "supplier.id"],
            name="fk_purchase_order_supplier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_purchase_order_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raised_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_purchase_order_raised_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_purchase_order_organization_id_code"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_purchase_order_organization_id_id"),
    )
    op.create_index(
        op.f("ix_purchase_order_organization_id"),
        "purchase_order",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_order_supplier",
        "purchase_order",
        ["organization_id", "supplier_id", "status"],
        unique=False,
    )
    op.create_table(
        "purchase_request_line",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("estimated_unit_price", sa.Numeric(precision=20, scale=4), nullable=True),
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
            "quantity > 0", name="ck_purchase_request_line_quantity"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "request_id"],
            ["purchase_request.organization_id", "purchase_request.id"],
            name="fk_purchase_request_line_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_purchase_request_line_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_request_line")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_purchase_request_line_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_purchase_request_line_organization_id"),
        "purchase_request_line",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_request_line_request",
        "purchase_request_line",
        ["organization_id", "request_id"],
        unique=False,
    )
    op.create_table(
        "supplier_payment",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "method",
            sa.Enum(
                "CASH", "BANK", "CARD", name="supplier_payment_method", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "RECORDED", "REVERSED", name="supplier_payment_status", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("paid_by_user_id", sa.UUID(), nullable=True),
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
            "amount > 0", name="ck_supplier_payment_amount"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_id"],
            ["supplier.organization_id", "supplier.id"],
            name="fk_supplier_payment_supplier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_supplier_payment_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paid_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_supplier_payment_paid_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_payment")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_supplier_payment_organization_id_code"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_supplier_payment_organization_id_id"),
    )
    op.create_index(
        op.f("ix_supplier_payment_organization_id"),
        "supplier_payment",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_supplier_payment_supplier",
        "supplier_payment",
        ["organization_id", "supplier_id", "status"],
        unique=False,
    )
    op.create_table(
        "goods_receipt",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("received_by_user_id", sa.UUID(), nullable=True),
        sa.Column("delivery_note", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id", "order_id"],
            ["purchase_order.organization_id", "purchase_order.id"],
            name="fk_goods_receipt_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_id"],
            ["supplier.organization_id", "supplier.id"],
            name="fk_goods_receipt_supplier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_goods_receipt_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["received_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_goods_receipt_received_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goods_receipt")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_goods_receipt_organization_id_code"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_goods_receipt_organization_id_id"),
    )
    op.create_index(
        op.f("ix_goods_receipt_organization_id"), "goods_receipt", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_goods_receipt_supplier",
        "goods_receipt",
        ["organization_id", "supplier_id"],
        unique=False,
    )
    op.create_table(
        "purchase_order_line",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=20, scale=4), nullable=False),
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
            "quantity > 0", name="ck_purchase_order_line_quantity"
        ),
        sa.CheckConstraint(
            "unit_price >= 0", name="ck_purchase_order_line_unit_price"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "order_id"],
            ["purchase_order.organization_id", "purchase_order.id"],
            name="fk_purchase_order_line_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_purchase_order_line_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_line")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_purchase_order_line_organization_id_id"
        ),
    )
    op.create_index(
        "ix_purchase_order_line_order",
        "purchase_order_line",
        ["organization_id", "order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_order_line_organization_id"),
        "purchase_order_line",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "supplier_invoice",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("supplier_reference", sa.String(length=64), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "MATERIALS", "EXPENSE", name="supplier_invoice_kind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "REGISTERED",
                "VOID",
                name="supplier_invoice_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_by_user_id", sa.UUID(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
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
            "(status = 'DRAFT') = (registered_at IS NULL)",
            name="ck_supplier_invoice_registered_has_time",
        ),
        sa.CheckConstraint(
            "(status = 'VOID') = (void_reason IS NOT NULL)",
            name="ck_supplier_invoice_void_has_reason",
        ),
        sa.CheckConstraint(
            "total >= 0", name="ck_supplier_invoice_total"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "order_id"],
            ["purchase_order.organization_id", "purchase_order.id"],
            name="fk_supplier_invoice_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_id"],
            ["supplier.organization_id", "supplier.id"],
            name="fk_supplier_invoice_supplier",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_supplier_invoice_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["registered_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_supplier_invoice_registered_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_invoice")),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_supplier_invoice_organization_id_code"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_supplier_invoice_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "supplier_id",
            "supplier_reference",
            name="uq_supplier_invoice_supplier_reference",
        ),
    )
    op.create_index(
        "ix_supplier_invoice_due", "supplier_invoice", ["organization_id", "due_date"], unique=False
    )
    op.create_index(
        op.f("ix_supplier_invoice_organization_id"),
        "supplier_invoice",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_supplier_invoice_supplier",
        "supplier_invoice",
        ["organization_id", "supplier_id", "status"],
        unique=False,
    )
    op.create_table(
        "goods_receipt_line",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("receipt_id", sa.UUID(), nullable=False),
        sa.Column("order_line_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("rejected_quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
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
            "quantity > 0", name="ck_goods_receipt_line_quantity"
        ),
        sa.CheckConstraint(
            "rejected_quantity = 0 OR rejected_reason IS NOT NULL",
            name="ck_goods_receipt_line_rejection_has_reason",
        ),
        sa.CheckConstraint(
            "rejected_quantity >= 0",
            name="ck_goods_receipt_line_rejected",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "order_line_id"],
            ["purchase_order_line.organization_id", "purchase_order_line.id"],
            name="fk_goods_receipt_line_order_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "receipt_id"],
            ["goods_receipt.organization_id", "goods_receipt.id"],
            name="fk_goods_receipt_line_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_goods_receipt_line_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goods_receipt_line")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_goods_receipt_line_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_goods_receipt_line_organization_id"),
        "goods_receipt_line",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_goods_receipt_line_receipt",
        "goods_receipt_line",
        ["organization_id", "receipt_id"],
        unique=False,
    )
    op.create_table(
        "supplier_payment_allocation",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_reason", sa.Text(), nullable=True),
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
            "(reversed_at IS NULL) = (reversed_reason IS NULL)",
            name=op.f(
                "ck_supplier_payment_allocation_ck_supplier_payment_allocation_reversal_has_reason"
            ),
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_supplier_payment_allocation_amount",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["supplier_invoice.organization_id", "supplier_invoice.id"],
            name="fk_supplier_payment_allocation_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["supplier_payment.organization_id", "supplier_payment.id"],
            name="fk_supplier_payment_allocation_payment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_supplier_payment_allocation_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_payment_allocation")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_supplier_payment_allocation_organization_id_id"
        ),
    )
    op.create_index(
        "ix_supplier_payment_allocation_invoice",
        "supplier_payment_allocation",
        ["organization_id", "invoice_id", "reversed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_payment_allocation_organization_id"),
        "supplier_payment_allocation",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "supplier_invoice_line",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("receipt_line_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=20, scale=4), nullable=False),
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
            "quantity > 0", name="ck_supplier_invoice_line_quantity"
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name="ck_supplier_invoice_line_unit_price",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["supplier_invoice.organization_id", "supplier_invoice.id"],
            name="fk_supplier_invoice_line_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "receipt_line_id"],
            ["goods_receipt_line.organization_id", "goods_receipt_line.id"],
            name="fk_supplier_invoice_line_receipt_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_supplier_invoice_line_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_invoice_line")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_supplier_invoice_line_organization_id_id"
        ),
    )
    op.create_index(
        "ix_supplier_invoice_line_invoice",
        "supplier_invoice_line",
        ["organization_id", "invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_invoice_line_organization_id"),
        "supplier_invoice_line",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_supplier_invoice_line_organization_id"), table_name="supplier_invoice_line"
    )
    op.drop_index("ix_supplier_invoice_line_invoice", table_name="supplier_invoice_line")
    op.drop_table("supplier_invoice_line")
    op.drop_index(
        op.f("ix_supplier_payment_allocation_organization_id"),
        table_name="supplier_payment_allocation",
    )
    op.drop_index(
        "ix_supplier_payment_allocation_invoice", table_name="supplier_payment_allocation"
    )
    op.drop_table("supplier_payment_allocation")
    op.drop_index("ix_goods_receipt_line_receipt", table_name="goods_receipt_line")
    op.drop_index(op.f("ix_goods_receipt_line_organization_id"), table_name="goods_receipt_line")
    op.drop_table("goods_receipt_line")
    op.drop_index("ix_supplier_invoice_supplier", table_name="supplier_invoice")
    op.drop_index(op.f("ix_supplier_invoice_organization_id"), table_name="supplier_invoice")
    op.drop_index("ix_supplier_invoice_due", table_name="supplier_invoice")
    op.drop_table("supplier_invoice")
    op.drop_index(op.f("ix_purchase_order_line_organization_id"), table_name="purchase_order_line")
    op.drop_index("ix_purchase_order_line_order", table_name="purchase_order_line")
    op.drop_table("purchase_order_line")
    op.drop_index("ix_goods_receipt_supplier", table_name="goods_receipt")
    op.drop_index(op.f("ix_goods_receipt_organization_id"), table_name="goods_receipt")
    op.drop_table("goods_receipt")
    op.drop_index("ix_supplier_payment_supplier", table_name="supplier_payment")
    op.drop_index(op.f("ix_supplier_payment_organization_id"), table_name="supplier_payment")
    op.drop_table("supplier_payment")
    op.drop_index("ix_purchase_request_line_request", table_name="purchase_request_line")
    op.drop_index(
        op.f("ix_purchase_request_line_organization_id"), table_name="purchase_request_line"
    )
    op.drop_table("purchase_request_line")
    op.drop_index("ix_purchase_order_supplier", table_name="purchase_order")
    op.drop_index(op.f("ix_purchase_order_organization_id"), table_name="purchase_order")
    op.drop_table("purchase_order")
    op.drop_index(op.f("ix_supplier_organization_id"), table_name="supplier")
    op.drop_index("ix_supplier_name", table_name="supplier")
    op.drop_table("supplier")
    op.drop_index("ix_purchase_request_status", table_name="purchase_request")
    op.drop_index(op.f("ix_purchase_request_organization_id"), table_name="purchase_request")
    op.drop_table("purchase_request")
