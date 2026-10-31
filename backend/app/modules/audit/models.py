"""The audit trail.

Two properties matter more than convenience here:

* **Append-only.** A database trigger (see the Phase 1 migration) rejects any
  UPDATE or DELETE on this table, so an ordinary application bug — or an
  ordinary user with full application rights — cannot rewrite history.
* **Self-contained.** Actor and target are recorded as identifiers *and* as
  human-readable labels, because the point of an audit record is to stay
  meaningful after the records it refers to have been renamed or deactivated.

`old_values`/`new_values` hold only the fields that actually changed, already
filtered through `app.core.logging.SENSITIVE_KEYS`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"

    #: NULL for platform-level events such as a failed sign-in attempt that
    #: never resolved to an organization.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT"),
        index=True,
    )
    #: Who acted, as text, kept readable even if the user row changes later.
    #: For unauthenticated events this is the submitted identifier.
    actor_label: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Dotted event name, e.g. ``accounting.journal.posted``.
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")

    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    target_label: Mapped[str | None] = mapped_column(String(255))

    #: The business event that caused this change, when there is one — a
    #: posting event id, an invoice number, a payroll run. This is what makes
    #: an entry traceable back to why it happened.
    business_event: Mapped[str | None] = mapped_column(String(128), index=True)
    #: Operator-supplied justification. Required for reversals, refunds,
    #: stock adjustments and period reopening.
    reason: Mapped[str | None] = mapped_column(Text)

    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)

    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="outcome_known"),
        Index("ix_audit_log_organization_id_occurred_at", "organization_id", "occurred_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )


__all__ = ["AuditLog"]
