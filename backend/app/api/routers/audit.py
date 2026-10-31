"""Read access to the audit trail.

Read-only by design: there is no endpoint that edits or deletes an audit
record, and the database rejects the attempt regardless.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.tenancy import scoped
from app.modules.audit.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_label: str
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    target_label: str | None
    business_event: str | None
    reason: str | None
    old_values: dict | None
    new_values: dict | None
    request_id: str | None
    ip_address: str | None
    occurred_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogResponse]
    total_returned: int
    #: Cursor for the next page: pass as `before` to continue.
    next_before: datetime | None


@router.get(
    "/logs",
    response_model=AuditLogPage,
    dependencies=[Depends(require_permissions("audit.log.read"))],
)
async def list_audit_logs(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    action: str | None = Query(default=None, max_length=96),
    target_type: str | None = Query(default=None, max_length=64),
    target_id: str | None = Query(default=None, max_length=64),
    outcome: str | None = Query(default=None, pattern="^(success|failure|denied)$"),
    before: datetime | None = Query(
        default=None, description="Return records older than this instant"
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> AuditLogPage:
    """Audit records for the caller's organization, newest first.

    Keyset pagination on `occurred_at`: an offset would skip or repeat rows as
    new events arrive while an auditor is paging through.
    """
    organization_id = context.require_organization()
    stmt = scoped(select(AuditLog), AuditLog, organization_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)
    if before:
        stmt = stmt.where(AuditLog.occurred_at < before)

    stmt = stmt.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    return AuditLogPage(
        items=[AuditLogResponse.model_validate(r) for r in rows],
        total_returned=len(rows),
        next_before=rows[-1].occurred_at if len(rows) == limit else None,
    )
