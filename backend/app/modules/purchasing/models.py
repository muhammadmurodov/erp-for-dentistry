"""Suppliers, requests, orders, receipts, bills and payments.

**Four records, not four flags.** Ordering, receiving, invoicing and paying
are separate tables with their own numbers and their own dates, because the
questions a clinic actually asks span them: what did we order that has not
arrived, what has arrived that we have not been billed for, what are we
billed for that we have not paid. A single `purchase` row with booleans
cannot answer any of those, and is how a supplier who invoices twice gets
paid twice.

**What is owed is derived, never stored.** A bill has no `paid` column and no
`remaining` column. What is left is the registered total minus its live
allocations, exactly as a customer invoice works (ADR-026). A stored balance
is a second answer that drifts from the rows it came from.

**This module stores no stock.** A goods receipt records what arrived in the
quantities it arrived in. What those quantities are *worth*, and where they
sit afterwards, is inventory's question and waits on the valuation method
(`OPEN_QUESTIONS.md` I1).
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.purchasing.enums import (
    BillKind,
    BillStatus,
    OrderStatus,
    RequestStatus,
    SupplierPaymentMethod,
    SupplierPaymentStatus,
)

#: Quantities are decimals: materials are bought by the box and used by the
#: millilitre, and an integer column would force a unit choice on both.
QuantityType = MoneyType


def _enum(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _org_scope(table: str) -> tuple:
    return (UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id"),)


def _org_column() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def _parent_fk(table: str, column: str, parent: str, name: str) -> ForeignKeyConstraint:
    """A composite foreign key, so a child cannot cross a tenant boundary."""
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{parent}.organization_id", f"{parent}.id"],
        ondelete="RESTRICT",
        name=name,
    )


class Supplier(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Somebody the clinic buys from."""

    __tablename__ = "supplier"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: STIR/INN. Free text and nullable: a market stall selling gypsum has
    #: none, and refusing to record the purchase would not change that.
    tax_number: Mapped[str | None] = mapped_column(String(32))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("supplier"),
        UniqueConstraint("organization_id", "code", name="uq_supplier_organization_id_code"),
        Index("ix_supplier_name", "organization_id", "name"),
    )


class PurchaseRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Somebody asking for something to be bought.

    Kept even in a clinic of three, because the request is where the reason
    lives — "the composite is nearly out" — and an order raised months later
    with no reason attached is an order nobody can justify to an auditor.
    """

    __tablename__ = "purchase_request"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        _enum(RequestStatus, "purchase_request_status"),
        nullable=False,
        default=RequestStatus.DRAFT,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    needed_by: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    #: Set when the person who asked also approved it, which a clinic of
    #: three sometimes cannot avoid (ADR-041). Recorded, never hidden.
    self_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        *_org_scope("purchase_request"),
        UniqueConstraint(
            "organization_id", "code", name="uq_purchase_request_organization_id_code"
        ),
        Index("ix_purchase_request_status", "organization_id", "status"),
    )


class PurchaseRequestLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "purchase_request_line"

    organization_id: Mapped[uuid.UUID] = _org_column()
    request_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16))
    #: What the asker thinks it costs. Never used for a posting: the order
    #: carries the agreed price, and the bill carries the charged one.
    estimated_unit_price: Mapped[Decimal | None] = mapped_column(MoneyType)

    __table_args__ = (
        *_org_scope("purchase_request_line"),
        _parent_fk(
            "purchase_request_line",
            "request_id",
            "purchase_request",
            "fk_purchase_request_line_request",
        ),
        CheckConstraint("quantity > 0", name="ck_purchase_request_line_quantity"),
        Index("ix_purchase_request_line_request", "organization_id", "request_id"),
    )


class PurchaseOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A commitment to a supplier. Posts nothing."""

    __tablename__ = "purchase_order"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "purchase_order_status"), nullable=False, default=OrderStatus.DRAFT
    )
    ordered_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_on: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    #: The agreed total, derived from the lines when they are set and stored
    #: because an order is a document: it says what was agreed on the day,
    #: and a later price change must not rewrite it.
    total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    #: Who committed the clinic to this. Stored rather than inferred from the
    #: audit trail: the segregation-of-duties check needs it on every
    #: approval, and a control that depends on reading the audit log is a
    #: control that breaks when the audit log is archived.
    raised_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    self_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    closed_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("purchase_order"),
        UniqueConstraint("organization_id", "code", name="uq_purchase_order_organization_id_code"),
        _parent_fk("purchase_order", "supplier_id", "supplier", "fk_purchase_order_supplier"),
        _parent_fk("purchase_order", "request_id", "purchase_request", "fk_purchase_order_request"),
        CheckConstraint("total >= 0", name="ck_purchase_order_total"),
        Index("ix_purchase_order_supplier", "organization_id", "supplier_id", "status"),
    )


class PurchaseOrderLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "purchase_order_line"

    organization_id: Mapped[uuid.UUID] = _org_column()
    order_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: The stocked material this line is for, when it is one. Null for
    #: anything the clinic does not keep on a shelf.
    item_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16))
    unit_price: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)

    __table_args__ = (
        *_org_scope("purchase_order_line"),
        _parent_fk(
            "purchase_order_line", "order_id", "purchase_order", "fk_purchase_order_line_order"
        ),
        _parent_fk(
            "purchase_order_line", "item_id", "inventory_item", "fk_purchase_order_line_item"
        ),
        CheckConstraint("quantity > 0", name="ck_purchase_order_line_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_purchase_order_line_unit_price"),
        Index("ix_purchase_order_line_order", "organization_id", "order_id"),
    )


class GoodsReceipt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What actually arrived, on the day it arrived.

    **Posts nothing**, and that is a decision rather than an omission. The
    standard accrual at receipt debits stock and credits a goods-received-
    not-invoiced account; the clinic's workbook has no such account and posts
    materials straight to 1010 against the payable when the invoice arrives.
    Inventing the account would be inventing a statutory classification, so
    the receipt records quantities and the bill does the posting. The gap is
    recorded as an open question rather than papered over.
    """

    __tablename__ = "goods_receipt"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    received_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    delivery_note: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("goods_receipt"),
        UniqueConstraint("organization_id", "code", name="uq_goods_receipt_organization_id_code"),
        _parent_fk("goods_receipt", "supplier_id", "supplier", "fk_goods_receipt_supplier"),
        _parent_fk("goods_receipt", "order_id", "purchase_order", "fk_goods_receipt_order"),
        Index("ix_goods_receipt_supplier", "organization_id", "supplier_id"),
    )


class GoodsReceiptLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "goods_receipt_line"

    organization_id: Mapped[uuid.UUID] = _org_column()
    receipt_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: Naming the item is what puts the delivery on a shelf: receiving
    #: writes a stock movement for every line that has one, so stock arrives
    #: through the same door the money does, and never by hand afterwards.
    item_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16))
    #: What was rejected on the doorstep — broken, wrong, out of date. Kept
    #: beside what was accepted so the delivery note can be reconciled with
    #: the invoice that follows it.
    rejected_quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False, default=0)
    rejected_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("goods_receipt_line"),
        _parent_fk(
            "goods_receipt_line", "receipt_id", "goods_receipt", "fk_goods_receipt_line_receipt"
        ),
        _parent_fk(
            "goods_receipt_line",
            "order_line_id",
            "purchase_order_line",
            "fk_goods_receipt_line_order_line",
        ),
        _parent_fk(
            "goods_receipt_line", "item_id", "inventory_item", "fk_goods_receipt_line_item"
        ),
        CheckConstraint("quantity > 0", name="ck_goods_receipt_line_quantity"),
        CheckConstraint("rejected_quantity >= 0", name="ck_goods_receipt_line_rejected"),
        CheckConstraint(
            "rejected_quantity = 0 OR rejected_reason IS NOT NULL",
            name="ck_goods_receipt_line_rejection_has_reason",
        ),
        Index("ix_goods_receipt_line_receipt", "organization_id", "receipt_id"),
    )


class SupplierInvoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What the supplier is charging. Registering it creates the payable."""

    __tablename__ = "supplier_invoice"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: The supplier's own number. Unique per supplier when given, which is
    #: what stops the same invoice being entered twice — the commonest way a
    #: small business pays for one delivery two times.
    supplier_reference: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[BillKind] = mapped_column(_enum(BillKind, "supplier_invoice_kind"), nullable=False)
    status: Mapped[BillStatus] = mapped_column(
        _enum(BillStatus, "supplier_invoice_status"), nullable=False, default=BillStatus.DRAFT
    )
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=0)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    void_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("supplier_invoice"),
        UniqueConstraint(
            "organization_id", "code", name="uq_supplier_invoice_organization_id_code"
        ),
        UniqueConstraint(
            "organization_id",
            "supplier_id",
            "supplier_reference",
            name="uq_supplier_invoice_supplier_reference",
        ),
        _parent_fk("supplier_invoice", "supplier_id", "supplier", "fk_supplier_invoice_supplier"),
        _parent_fk("supplier_invoice", "order_id", "purchase_order", "fk_supplier_invoice_order"),
        CheckConstraint("total >= 0", name="ck_supplier_invoice_total"),
        CheckConstraint(
            "(status = 'VOID') = (void_reason IS NOT NULL)",
            name="ck_supplier_invoice_void_has_reason",
        ),
        CheckConstraint(
            "(status = 'DRAFT') = (registered_at IS NULL)",
            name="ck_supplier_invoice_registered_has_time",
        ),
        Index("ix_supplier_invoice_supplier", "organization_id", "supplier_id", "status"),
        Index("ix_supplier_invoice_due", "organization_id", "due_date"),
    )


class SupplierInvoiceLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "supplier_invoice_line"

    organization_id: Mapped[uuid.UUID] = _org_column()
    invoice_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16))
    unit_price: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)

    __table_args__ = (
        *_org_scope("supplier_invoice_line"),
        _parent_fk(
            "supplier_invoice_line",
            "invoice_id",
            "supplier_invoice",
            "fk_supplier_invoice_line_invoice",
        ),
        _parent_fk(
            "supplier_invoice_line",
            "receipt_line_id",
            "goods_receipt_line",
            "fk_supplier_invoice_line_receipt_line",
        ),
        CheckConstraint("quantity > 0", name="ck_supplier_invoice_line_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_supplier_invoice_line_unit_price"),
        Index("ix_supplier_invoice_line_invoice", "organization_id", "invoice_id"),
    )


class SupplierPayment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Money paid to a supplier.

    Carries no invoice. Money handed over is money handed over; which bills
    it settles is a separate decision recorded in `supplier_payment_allocation`
    — and the part not yet allocated is an **advance**, exactly as a patient's
    unapplied payment is. That symmetry is deliberate: the clinic pays its
    suppliers in advance as a matter of course, and a payment model that
    demanded an invoice could not record it.
    """

    __tablename__ = "supplier_payment"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    method: Mapped[SupplierPaymentMethod] = mapped_column(
        _enum(SupplierPaymentMethod, "supplier_payment_method"), nullable=False
    )
    status: Mapped[SupplierPaymentStatus] = mapped_column(
        _enum(SupplierPaymentStatus, "supplier_payment_status"),
        nullable=False,
        default=SupplierPaymentStatus.RECORDED,
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    paid_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("supplier_payment"),
        UniqueConstraint(
            "organization_id", "code", name="uq_supplier_payment_organization_id_code"
        ),
        _parent_fk("supplier_payment", "supplier_id", "supplier", "fk_supplier_payment_supplier"),
        CheckConstraint("amount > 0", name="ck_supplier_payment_amount"),
        Index("ix_supplier_payment_supplier", "organization_id", "supplier_id", "status"),
    )


class SupplierPaymentAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Part of a payment, applied to one bill.

    Reversed rather than deleted, like a customer allocation: a row that
    disappears takes the reason with it.
    """

    __tablename__ = "supplier_payment_allocation"

    organization_id: Mapped[uuid.UUID] = _org_column()
    payment_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("supplier_payment_allocation"),
        _parent_fk(
            "supplier_payment_allocation",
            "payment_id",
            "supplier_payment",
            "fk_supplier_payment_allocation_payment",
        ),
        _parent_fk(
            "supplier_payment_allocation",
            "invoice_id",
            "supplier_invoice",
            "fk_supplier_payment_allocation_invoice",
        ),
        CheckConstraint("amount > 0", name="ck_supplier_payment_allocation_amount"),
        CheckConstraint(
            "(reversed_at IS NULL) = (reversed_reason IS NULL)",
            name="ck_supplier_payment_allocation_reversal_has_reason",
        ),
        Index(
            "ix_supplier_payment_allocation_invoice",
            "organization_id",
            "invoice_id",
            "reversed_at",
        ),
    )
