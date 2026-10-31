"""Cash register, session and movement records.

Expected cash is deliberately **not** a column. It is the sum of the
session's movements, computed when it is needed, so it cannot be edited into
agreement with a count that does not match. That is the whole point of a cash
reconciliation: the two figures are produced independently and the difference
is recorded.
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
from app.modules.cash.enums import DepositRoute, MovementKind, SessionStatus


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


class CashRegister(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A physical till.

    `account_id` is the ledger account its balance belongs to — normally
    5010. Keeping it explicit means a second till can post to a second
    account without any code change.
    """

    __tablename__ = "cash_register"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("cash_register"),
        UniqueConstraint("organization_id", "code", name="uq_cash_register_organization_id_code"),
        ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["account.organization_id", "account.id"],
            ondelete="RESTRICT",
            name="fk_cash_register_account",
        ),
    )


class CashSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A till's working period, from opening float to reconciled close.

    A partial unique index (added by the migration) allows **one open session
    per register**. Two open sessions would make "expected cash" meaningless,
    and no application check can be relied on to prevent it under
    concurrency.
    """

    __tablename__ = "cash_session"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    register_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: The trading day this session belongs to, in the organization's
    #: timezone. Not derived from a timestamp: a session that runs past
    #: midnight still belongs to the day it opened.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "cash_session_status"),
        nullable=False,
        default=SessionStatus.OPEN,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    #: What a person physically counted. NULL until a count is submitted.
    counted_amount: Mapped[Decimal | None] = mapped_column(MoneyType)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    #: Counted minus expected, stored at the moment of counting so the
    #: reconciliation is a record of what was found, not a live calculation
    #: that would change if a movement were added later.
    discrepancy: Mapped[Decimal | None] = mapped_column(MoneyType)
    discrepancy_reason: Mapped[str | None] = mapped_column(Text)

    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        *_org_scope("cash_session"),
        ForeignKeyConstraint(
            ["organization_id", "register_id"],
            ["cash_register.organization_id", "cash_register.id"],
            ondelete="RESTRICT",
            name="fk_cash_session_register",
        ),
        CheckConstraint(
            "counted_amount IS NULL OR counted_amount >= 0",
            name="counted_not_negative",
        ),
        CheckConstraint("status <> 'CLOSED' OR closed_at IS NOT NULL", name="closed_has_timestamp"),
        CheckConstraint(
            "counted_amount IS NOT NULL OR discrepancy IS NULL",
            name="discrepancy_needs_a_count",
        ),
        # One open session per register, enforced by a partial unique index.
        # Declared here as well as created by the migration so that
        # `alembic check` sees no drift between the models and the schema.
        Index(
            "uq_cash_session_one_open_per_register",
            "organization_id",
            "register_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_cash_session_register", "organization_id", "register_id", "status"),
        Index("ix_cash_session_date", "organization_id", "business_date"),
    )


class CashMovement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One movement of cash into or out of a session.

    Amounts are always positive; `kind` decides the direction. A posted
    movement is never deleted — a mistake is corrected with an offsetting
    adjustment, which leaves both records visible.
    """

    __tablename__ = "cash_movement"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[MovementKind] = mapped_column(
        _enum(MovementKind, "cash_movement_kind"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Set when this movement is a customer payment. The link is held here,
    #: on the cash side, so that `sales` need not know cash sessions exist.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: Only meaningful for a DEPOSIT: which transit account it posts to.
    deposit_route: Mapped[DepositRoute | None] = mapped_column(
        _enum(DepositRoute, "cash_deposit_route")
    )
    reference: Mapped[str | None] = mapped_column(String(128))
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("cash_movement"),
        ForeignKeyConstraint(
            ["organization_id", "session_id"],
            ["cash_session.organization_id", "cash_session.id"],
            ondelete="RESTRICT",
            name="fk_cash_movement_session",
        ),
        ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["payment.organization_id", "payment.id"],
            ondelete="RESTRICT",
            name="fk_cash_movement_payment",
        ),
        # One movement per payment: a payment cannot be counted into the
        # drawer twice, whatever the caller does.
        UniqueConstraint("organization_id", "payment_id", name="uq_cash_movement_payment"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "kind <> 'DEPOSIT' OR deposit_route IS NOT NULL",
            name="deposit_needs_a_route",
        ),
        Index("ix_cash_movement_session", "organization_id", "session_id", "kind"),
    )


__all__ = ["CashMovement", "CashRegister", "CashSession"]
