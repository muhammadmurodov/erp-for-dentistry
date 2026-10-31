"""Sales records: catalogue, customers, invoices, payments, allocations.

The shapes here follow from one rule: **an amount is recorded once, in one
place, and everything else references it.** An invoice's balance is not a
stored field that code remembers to update; it is the invoice total minus the
sum of its allocations. A payment's unallocated amount is the same idea. That
is why `PaymentAllocation` exists as a table rather than as a column on either
side.
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
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.sales.enums import (
    CustomerKind,
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    ServiceCategory,
)


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


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Whoever is billed.

    For the clinic this is the patient as payer — or whoever pays on their
    behalf. It is **not** the clinical record: that lives in the clinic
    module behind `clinic.clinical.read`, so billing staff never acquire
    treatment detail by virtue of raising an invoice.
    """

    __tablename__ = "customer"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[CustomerKind] = mapped_column(
        _enum(CustomerKind, "customer_kind"), nullable=False, default=CustomerKind.PERSON
    )
    tax_number: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("customer"),
        UniqueConstraint("organization_id", "code", name="uq_customer_organization_id_code"),
        CheckConstraint(
            "tax_number IS NULL OR tax_number ~ '^[0-9]{9,14}$'",
            name="tax_number_digits",
        ),
        Index("ix_customer_name", "organization_id", "display_name"),
    )


class Service(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Something the organization sells.

    `mxik_code` is the statutory ИКПУ classification that must appear on a
    fiscal receipt. It is recorded here so a receipt can be produced from an
    invoice line without a second lookup.
    """

    __tablename__ = "service"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz: Mapped[str | None] = mapped_column(String(255))
    name_ru: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[ServiceCategory] = mapped_column(
        _enum(ServiceCategory, "service_category"),
        nullable=False,
        default=ServiceCategory.TREATMENT,
    )
    #: ИКПУ / MXIK — statutory product classification for fiscal receipts.
    mxik_code: Mapped[str | None] = mapped_column(String(32))
    unit_code: Mapped[str | None] = mapped_column(String(16))
    unit_label: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("service"),
        UniqueConstraint("organization_id", "code", name="uq_service_organization_id_code"),
        Index("ix_service_active", "organization_id", "is_active"),
    )


class ServicePrice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A price, valid over a date range.

    Prices are versioned rather than overwritten so that an invoice raised
    last March can be explained by the price list that applied last March.
    An invoice line stores the price it actually used, so a later price
    change cannot restate it — the same principle as accounting policy
    versioning (ADR-011).
    """

    __tablename__ = "service_price"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    service_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        *_org_scope("service_price"),
        ForeignKeyConstraint(
            ["organization_id", "service_id"],
            ["service.organization_id", "service.id"],
            ondelete="CASCADE",
            name="fk_service_price_service",
        ),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range_ordered",
        ),
        Index(
            "ix_service_price_lookup",
            "organization_id",
            "service_id",
            "effective_from",
        ),
    )


class Invoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A demand for payment.

    Issuing an invoice is the moment revenue is recognised and a receivable
    comes into existence — not when the service was completed, and not when
    the money arrives.

    The outstanding balance is deliberately **not** a column. It is
    `total - sum(allocations)`, computed where it is needed, so it cannot
    drift from the allocation records the way a cached field would.
    """

    __tablename__ = "invoice"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status"), nullable=False, default=InvoiceStatus.DRAFT
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")

    #: Line totals before tax.
    subtotal: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("0"))
    #: Tax extracted or added, per the organization's declared treatment.
    #: Zero while that treatment is `NONE` — see OPEN_QUESTIONS A5.
    tax_total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("0"))
    #: Recorded for traceability: which treatment produced the figures above.
    tax_treatment: Mapped[str] = mapped_column(String(32), nullable=False, default="UNDECLARED")

    notes: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("invoice"),
        UniqueConstraint("organization_id", "number", name="uq_invoice_organization_id_number"),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customer.organization_id", "customer.id"],
            ondelete="RESTRICT",
            name="fk_invoice_customer",
        ),
        CheckConstraint(
            "subtotal >= 0 AND tax_total >= 0 AND total >= 0",
            name="amounts_not_negative",
        ),
        CheckConstraint("total = subtotal + tax_total", name="total_is_subtotal_plus_tax"),
        CheckConstraint("due_date IS NULL OR due_date >= issue_date", name="due_not_before_issue"),
        CheckConstraint("status <> 'ISSUED' OR issued_at IS NOT NULL", name="issued_has_timestamp"),
        Index("ix_invoice_customer", "organization_id", "customer_id", "status"),
        Index("ix_invoice_issue_date", "organization_id", "issue_date"),
    )


class InvoiceLine(Base, UUIDPrimaryKeyMixin):
    """One billed item.

    `unit_price` is copied from the price list at issue, not referenced, so a
    later price change cannot restate a historical invoice.
    """

    __tablename__ = "invoice_line"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    #: Copied from the service so a fiscal receipt can be produced from the
    #: invoice alone, even if the catalogue entry changes later.
    mxik_code: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        *_org_scope("invoice_line"),
        UniqueConstraint(
            "organization_id", "invoice_id", "line_number", name="uq_invoice_line_number"
        ),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoice.organization_id", "invoice.id"],
            ondelete="CASCADE",
            name="fk_invoice_line_invoice",
        ),
        ForeignKeyConstraint(
            ["organization_id", "service_id"],
            ["service.organization_id", "service.id"],
            ondelete="RESTRICT",
            name="fk_invoice_line_service",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_not_negative"),
        CheckConstraint("line_total >= 0", name="line_total_not_negative"),
        CheckConstraint("line_number >= 1", name="line_number_positive"),
    )


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Money received from a customer.

    A payment exists independently of any invoice. That is what makes an
    **advance** representable: money taken before there is anything to apply
    it to is an unallocated payment, which is a liability — not revenue.
    """

    __tablename__ = "payment"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        _enum(PaymentMethod, "payment_method"), nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus, "payment_status"),
        nullable=False,
        default=PaymentStatus.RECORDED,
    )
    #: The gross amount the customer paid. For a card payment this is the
    #: amount on the receipt, not the amount the bank later settles.
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    reference: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)

    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_reason: Mapped[str | None] = mapped_column(Text)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("payment"),
        UniqueConstraint("organization_id", "number", name="uq_payment_organization_id_number"),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customer.organization_id", "customer.id"],
            ondelete="RESTRICT",
            name="fk_payment_customer",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_payment_customer", "organization_id", "customer_id"),
        Index("ix_payment_received_on", "organization_id", "received_on"),
    )


class PaymentAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """How much of one payment is applied to one invoice.

    Explicit rows rather than a balance column on either side. Three
    consequences, all wanted:

    * A payment may be split across several invoices, and an invoice may be
      settled by several payments, without either side having to know.
    * Over-allocation is detectable by summing, under a row lock.
    * The same payment cannot be applied to the same invoice twice — the
      unique constraint refuses it, so a double-click or a duplicated request
      cannot settle an invoice twice over.
    """

    __tablename__ = "payment_allocation"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    allocated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("payment_allocation"),
        UniqueConstraint(
            "organization_id",
            "payment_id",
            "invoice_id",
            name="uq_payment_allocation_payment_invoice",
        ),
        ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["payment.organization_id", "payment.id"],
            ondelete="CASCADE",
            name="fk_payment_allocation_payment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoice.organization_id", "invoice.id"],
            ondelete="RESTRICT",
            name="fk_payment_allocation_invoice",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_payment_allocation_invoice", "organization_id", "invoice_id"),
    )


__all__ = [
    "Customer",
    "Invoice",
    "InvoiceLine",
    "Payment",
    "PaymentAllocation",
    "Service",
    "ServicePrice",
]
