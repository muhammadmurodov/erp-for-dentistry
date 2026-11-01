"""FastAPI dependencies: database session, audit recorder, caller identity.

`require_permissions` is the only authorization gate in the HTTP layer, and
every write endpoint declares one. A denied attempt is audited before the 403
leaves the process, so an attempt to exceed authority is always on record.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import get_db_session, get_session_factory
from app.core.errors import AuthenticationFailed, PermissionDenied
from app.core.logging import actor_id_var, organization_id_var, request_id_var
from app.core.security import decode_access_token
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder, record_independently
from app.modules.organization.models import Organization

bearer_scheme = HTTPBearer(auto_error=False, description="Access token")

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def client_ip(request: Request) -> str | None:
    """Best-effort client address.

    `X-Forwarded-For` is trusted only because this service is expected to run
    behind a reverse proxy that sets it; see SECURITY.md. The value is used for
    audit context and rate-limit keys, never for authorization.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


async def get_audit_recorder(request: Request, session: DbSession) -> AsyncIterator[AuditRecorder]:
    recorder = AuditRecorder(
        session,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or None),
    )
    request.state.audit = recorder
    yield recorder


AuditDep = Annotated[AuditRecorder, Depends(get_audit_recorder)]


async def get_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    audit: AuditDep,
) -> RequestContext:
    """Resolve the caller from the bearer token.

    Permissions come from the token's `prm` claim rather than a fresh database
    read. That is a deliberate trade: a revoked role stays effective until the
    access token expires (15 minutes by default), in exchange for not querying
    the role tables on every request. Anything that must take effect at once —
    deactivating a user, ending a session — also revokes the refresh chain, so
    the longest window is one access-token lifetime.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationFailed("Authentication required")

    claims = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationFailed("Access token is malformed") from exc

    organization_id = uuid.UUID(claims["org"]) if claims.get("org") else None
    membership_id = uuid.UUID(claims["mem"]) if claims.get("mem") else None

    context = RequestContext(
        user_id=user_id,
        email=claims.get("eml", ""),
        full_name="",
        permissions=frozenset(claims.get("prm") or ()),
        organization_id=organization_id,
        membership_id=membership_id,
        is_superuser=bool(claims.get("sup")),
        request_id=request_id_var.get(),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    actor_id_var.set(str(user_id))
    if organization_id:
        organization_id_var.set(str(organization_id))
    audit.bind(
        actor_user_id=user_id,
        actor_label=claims.get("eml") or str(user_id),
        organization_id=organization_id,
    )
    request.state.context = context
    return context


Ctx = Annotated[RequestContext, Depends(get_context)]


def require_permissions(*permissions: str) -> Callable[..., object]:
    """Dependency factory gating an endpoint behind permission codes."""

    async def dependency(request: Request, context: Ctx) -> RequestContext:
        try:
            context.require(*permissions)
        except PermissionDenied:
            # Written in its own transaction: the request is about to fail and
            # its transaction will roll back, but the denial must survive.
            await record_independently(
                get_session_factory(),
                events.PERMISSION_DENIED,
                outcome="denied",
                actor_user_id=context.user_id,
                actor_label=context.email or str(context.user_id),
                organization_id=context.organization_id,
                target_type="endpoint",
                target_label=f"{request.method} {request.url.path}",
                reason="missing permission",
                new_values={"required": sorted(permissions)},
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            )
            raise
        return context

    return dependency


def require_any_permission(*permissions: str) -> Callable[..., object]:
    """Like `require_permissions`, but one of the codes is enough.

    For an endpoint whose authority depends on something the gate cannot see —
    a refund needs approval authority, or the request side of it in an
    organization too small to separate the two (ADR-041). The gate admits
    either and the handler decides, so the weaker permission still cannot
    reach the endpoint and a denial is still recorded before the 403 leaves.
    """

    async def dependency(request: Request, context: Ctx) -> RequestContext:
        if not context.has_any(*permissions):
            await record_independently(
                get_session_factory(),
                events.PERMISSION_DENIED,
                outcome="denied",
                actor_user_id=context.user_id,
                actor_label=context.email or str(context.user_id),
                organization_id=context.organization_id,
                target_type="endpoint",
                target_label=f"{request.method} {request.url.path}",
                reason="missing permission",
                new_values={"required_any_of": sorted(permissions)},
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            )
            raise PermissionDenied(
                "You do not have permission to perform this action",
                details={"required_any_of": sorted(permissions)},
            )
        return context

    return dependency


async def require_organization_context(context: Ctx) -> RequestContext:
    """Reject a request that has not selected an organization."""
    context.require_organization()
    return context


OrgCtx = Annotated[RequestContext, Depends(require_organization_context)]


async def duties_cannot_be_separated(context: OrgCtx, db: DbSession) -> bool:
    """Has this organization declared it has too few people to separate duties?

    Read here rather than taken from the caller, so an endpoint cannot be
    talked into relaxing a control by a request body, and resolved once per
    request rather than inside each service. A service that is never told
    defaults to the strict rule, which is the safe direction to fail in
    (ADR-041).
    """
    organization = await db.get(Organization, context.require_organization())
    return bool(organization and organization.duties_cannot_be_separated)


#: True when one person may both do a thing and approve it here.
DutySeparation = Annotated[bool, Depends(duties_cannot_be_separated)]
