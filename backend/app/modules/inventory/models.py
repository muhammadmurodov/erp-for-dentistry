"""Materials, where they are kept, and the ledger of everything that moved.

**Stock is a ledger, not a column.** There is no `quantity_on_hand`
anywhere in this module. What is on a shelf is the sum of the movements that
put it there and took it away, exactly as an account balance is the sum of
its journal lines. A stored total is a second answer to a question the rows
already answer, and it drifts — usually silently, and usually found by
somebody blaming the person who counted.

**Movements carry quantities, never amounts.** What materials cost is
already in the books: account 1010, debited when the supplier invoice was
registered. A cost column here would be a second place deciding what stock
is worth, and the two would disagree the first time a bill was corrected.
What stock is *worth* is answered by composing this module's quantities with
purchasing's prices, and only once an accountant has declared the valuation
method (`OPEN_QUESTIONS.md` I1).

**Nothing here posts to the ledger.** Receiving posts nothing by decision
(see `purchasing/services/receiving.py`), and consuming a material posts
nothing either while the valuation method is undeclared — the cost of
materials reaches the books through the supplier invoice that bought them.
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.inventory.enums import (
    AdjustmentStatus,
    StockMovementKind,
    StockMovementSource,
)

#: Quantities are decimals: a material is bought by the box and used by the
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


class InventoryCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A grouping a clinic would recognise: composites, anaesthetics, gloves."""

    __tablename__ = "inventory_category"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("inventory_category"),
        UniqueConstraint("organization_id", "code", name="uq_inventory_category_code"),
    )


class StockLocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Somewhere stock is kept — the store cupboard, the surgery, the fridge.

    A clinic of three has two or three of these, and the separation still
    earns its place: what is in the fridge has an expiry date somebody must
    look at, and what is in the surgery is what ran out mid-treatment.
    """

    __tablename__ = "stock_location"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Where a delivery lands, and where a treatment draws from, unless the
    #: person recording it says otherwise.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("stock_location"),
        UniqueConstraint("organization_id", "code", name="uq_stock_location_code"),
        Index(
            "uq_stock_location_single_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


class InventoryItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A material the clinic buys, keeps and uses up.

    `unit` is free text rather than a table of its own: a clinic of three
    buys in boxes, millilitres, pieces and grammes, and a reference table of
    units it would have to maintain is ceremony for six values. It is stated
    once here and then displayed everywhere the item appears, so a quantity
    is never shown without saying what it counts.
    """

    __tablename__ = "inventory_item"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Below this, the item is reported as low. Null means nobody has said
    #: what "low" is for this item, so it is never flagged — a made-up
    #: threshold is worse than none, because it trains people to ignore it.
    reorder_level: Mapped[Decimal | None] = mapped_column(QuantityType)
    #: Whether arrivals must name a batch. True for anything with an expiry
    #: date the clinic is answerable for; false for gloves.
    is_batch_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("inventory_item"),
        UniqueConstraint("organization_id", "code", name="uq_inventory_item_code"),
        _parent_fk(
            "inventory_item", "category_id", "inventory_category", "fk_inventory_item_category"
        ),
        CheckConstraint(
            "reorder_level IS NULL OR reorder_level >= 0",
            name="ck_inventory_item_reorder_level",
        ),
        Index("ix_inventory_item_name", "organization_id", "name"),
    )


class StockBatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A lot of one item, with the expiry date printed on it.

    Only items marked `is_batch_tracked` have these. The batch is what makes
    "which of these is out of date" answerable, and it is the reason an
    expiry report can exist at all.
    """

    __tablename__ = "stock_batch"

    organization_id: Mapped[uuid.UUID] = _org_column()
    item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("stock_batch"),
        _parent_fk("stock_batch", "item_id", "inventory_item", "fk_stock_batch_item"),
        UniqueConstraint("organization_id", "item_id", "code", name="uq_stock_batch_item_code"),
        Index("ix_stock_batch_expiry", "organization_id", "expires_on"),
    )


class StockMovement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One quantity, moving, with the record that caused it.

    This table is the whole of what the clinic has in stock. Every row is
    signed, every row names the record it came from, and no row is ever
    updated or deleted — a mistake is corrected by an approved adjustment,
    which leaves both the error and the correction on the ledger.
    """

    __tablename__ = "stock_movement"

    organization_id: Mapped[uuid.UUID] = _org_column()
    item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[StockMovementKind] = mapped_column(
        _enum(StockMovementKind, "stock_movement_kind"), nullable=False
    )
    #: Signed: positive puts stock on the shelf, negative takes it off.
    quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    source_kind: Mapped[StockMovementSource] = mapped_column(
        _enum(StockMovementSource, "stock_movement_source"), nullable=False
    )
    #: The id of the record named by `source_kind`. Not a foreign key,
    #: because it points at five different tables; the service that writes it
    #: has already loaded the record it refers to.
    source_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        *_org_scope("stock_movement"),
        _parent_fk("stock_movement", "item_id", "inventory_item", "fk_stock_movement_item"),
        _parent_fk("stock_movement", "location_id", "stock_location", "fk_stock_movement_location"),
        _parent_fk("stock_movement", "batch_id", "stock_batch", "fk_stock_movement_batch"),
        CheckConstraint("quantity <> 0", name="ck_stock_movement_quantity_not_zero"),
        # The sign and the reason must agree. A receipt that takes stock away
        # is not a receipt, whatever the form that wrote it believed.
        CheckConstraint(
            "(kind IN ('RECEIPT', 'TRANSFER_IN') AND quantity > 0) "
            "OR (kind IN ('CONSUMPTION', 'WRITE_OFF', 'TRANSFER_OUT') AND quantity < 0) "
            "OR kind = 'ADJUSTMENT'",
            name="ck_stock_movement_sign_matches_kind",
        ),
        # Stock does not leave for no reason. An adjustment or a write-off
        # says why, in the words of whoever decided it.
        CheckConstraint(
            "kind NOT IN ('ADJUSTMENT', 'WRITE_OFF') OR reason IS NOT NULL",
            name="ck_stock_movement_reason_required",
        ),
        Index("ix_stock_movement_item", "organization_id", "item_id", "location_id"),
        Index("ix_stock_movement_occurred", "organization_id", "occurred_on"),
        Index("ix_stock_movement_source", "organization_id", "source_kind", "source_id"),
    )


class StockAdjustment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A physical count, and the difference it found.

    Counting is one act and approving the difference is another, held apart
    by `inventory.adjustment.approve`, which the inventory manager
    deliberately does not hold. Nothing moves while this is draft or
    proposed: approval is the moment the ledger changes.
    """

    __tablename__ = "stock_adjustment"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    counted_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AdjustmentStatus] = mapped_column(
        _enum(AdjustmentStatus, "stock_adjustment_status"),
        nullable=False,
        default=AdjustmentStatus.DRAFT,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    counted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("stock_adjustment"),
        _parent_fk(
            "stock_adjustment", "location_id", "stock_location", "fk_stock_adjustment_location"
        ),
        UniqueConstraint("organization_id", "code", name="uq_stock_adjustment_code"),
        # A decided adjustment knows who decided it and when; an undecided
        # one cannot claim either.
        CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED')) = (decided_at IS NOT NULL)",
            name="ck_stock_adjustment_decided_has_time",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR decision_note IS NOT NULL",
            name="ck_stock_adjustment_rejection_has_note",
        ),
        Index("ix_stock_adjustment_status", "organization_id", "status"),
    )


class StockAdjustmentLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One item counted, what the system thought, and what was on the shelf.

    `system_quantity` is a snapshot taken at the moment of counting, kept so
    the count can be read back months later and still make sense. It is
    never used to compute the new balance — approval writes a movement for
    the difference and the balance is the sum of the movements, as always.
    """

    __tablename__ = "stock_adjustment_line"

    organization_id: Mapped[uuid.UUID] = _org_column()
    adjustment_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    system_quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    counted_quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("stock_adjustment_line"),
        _parent_fk(
            "stock_adjustment_line",
            "adjustment_id",
            "stock_adjustment",
            "fk_stock_adjustment_line_adjustment",
        ),
        _parent_fk(
            "stock_adjustment_line", "item_id", "inventory_item", "fk_stock_adjustment_line_item"
        ),
        _parent_fk(
            "stock_adjustment_line", "batch_id", "stock_batch", "fk_stock_adjustment_line_batch"
        ),
        CheckConstraint("counted_quantity >= 0", name="ck_stock_adjustment_line_counted"),
        UniqueConstraint(
            "organization_id",
            "adjustment_id",
            "item_id",
            "batch_id",
            name="uq_stock_adjustment_line_once",
        ),
        Index("ix_stock_adjustment_line_adjustment", "organization_id", "adjustment_id"),
    )
