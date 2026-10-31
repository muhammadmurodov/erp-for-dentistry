"""Writing audit records.

The recorder is handed to services rather than reached for globally, so a unit
test can assert exactly which events a workflow emitted.

Audit rows join the caller's transaction on purpose. If a posting rolls back,
its "posted" audit record must roll back with it — an audit trail that claims
things happened which did not is worse than none. Events that must survive a
failed request (a denied permission, a failed sign-in) are written by
`record_independently`, which uses its own short transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import (
    SENSITIVE_KEYS,
    get_logger,
    request_id_var,
)
from app.modules.audit.models import AuditLog

log = get_logger(__name__)

MAX_VALUE_LENGTH = 2000


def _scrub(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop sensitive keys and truncate oversized values."""
    if not values:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if key.lower() in SENSITIVE_KEYS:
            cleaned[key] = "[redacted]"
            continue
        if isinstance(value, uuid.UUID):
            cleaned[key] = str(value)
        elif isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
            cleaned[key] = value[:MAX_VALUE_LENGTH] + "…"
        elif isinstance(value, (str, int, bool, type(None), list, dict)):
            cleaned[key] = value
        else:
            # Decimal, date, datetime and friends: store the exact text form
            # rather than risk a float round trip through JSON.
            cleaned[key] = str(value)
    return cleaned


def changed_fields(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reduce two snapshots to only the keys whose value actually differs."""
    keys = set(before) | set(after)
    old = {k: before.get(k) for k in keys if before.get(k) != after.get(k)}
    new = {k: after.get(k) for k in keys if before.get(k) != after.get(k)}
    return old, new


class AuditRecorder:
    """Collects audit records for one request and flushes them with the work."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        actor_user_id: uuid.UUID | None = None,
        actor_label: str = "anonymous",
        organization_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._session = session
        self.actor_user_id = actor_user_id
        self.actor_label = actor_label
        self.organization_id = organization_id
        self.ip_address = ip_address
        self.user_agent = user_agent

    def bind(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        actor_label: str | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> None:
        """Fill in the actor once authentication has resolved it."""
        if actor_user_id is not None:
            self.actor_user_id = actor_user_id
        if actor_label is not None:
            self.actor_label = actor_label
        if organization_id is not None:
            self.organization_id = organization_id

    def record(
        self,
        action: str,
        *,
        outcome: str = "success",
        target_type: str | None = None,
        target_id: str | uuid.UUID | None = None,
        target_label: str | None = None,
        business_event: str | None = None,
        reason: str | None = None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        organization_id: uuid.UUID | None = None,
        actor_label: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> AuditLog:
        """Add an audit row to the current transaction."""
        entry = AuditLog(
            organization_id=organization_id or self.organization_id,
            actor_user_id=actor_user_id if actor_user_id is not None else self.actor_user_id,
            actor_label=actor_label or self.actor_label,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            target_label=target_label,
            business_event=business_event,
            reason=reason,
            old_values=_scrub(old_values),
            new_values=_scrub(new_values),
            request_id=request_id_var.get(),
            ip_address=self.ip_address,
            user_agent=self.user_agent,
        )
        self._session.add(entry)
        log.info(
            "audit.recorded",
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
        )
        return entry


async def record_independently(
    session_factory: Any,
    action: str,
    **kwargs: Any,
) -> None:
    """Write one audit record in its own transaction.

    Used for events that must be durable even though the request they belong
    to is about to fail — a rejected sign-in, a denied permission. Never let
    an audit failure mask the original error, so exceptions are logged and
    swallowed here.
    """
    try:
        async with session_factory() as session:
            recorder = AuditRecorder(
                session,
                actor_label=kwargs.pop("actor_label", "anonymous"),
                actor_user_id=kwargs.pop("actor_user_id", None),
                organization_id=kwargs.pop("organization_id", None),
                ip_address=kwargs.pop("ip_address", None),
                user_agent=kwargs.pop("user_agent", None),
            )
            recorder.record(action, **kwargs)
            await session.commit()
    except Exception:  # pragma: no cover - audit must not mask the real error
        log.exception("audit.independent_write_failed", action=action)
