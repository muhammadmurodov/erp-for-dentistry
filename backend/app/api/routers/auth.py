"""Authentication endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuditDep, Ctx, DbSession, client_ip
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.rate_limit import get_limiter
from app.modules.identity.schemas import (
    ChangePasswordRequest,
    CurrentUserResponse,
    LoginRequest,
    MembershipSummary,
    RefreshRequest,
    SwitchOrganizationRequest,
    TokenResponse,
)
from app.modules.identity.service import AuthenticatedSession, IdentityService
from app.modules.organization.models import Organization

router = APIRouter(prefix="/auth", tags=["auth"])


@asynccontextmanager
async def persist_security_side_effects(db: AsyncSession) -> AsyncIterator[None]:
    """Keep the side effects of a *rejected* authentication attempt.

    Two things a failed sign-in must leave behind: the audit record, and the
    incremented failure counter that drives account lockout. Both are written
    into this session by the service, and the global error handler rolls the
    request's transaction back — which would discard them and make lockout
    unreachable. Committing here before re-raising keeps them.

    Only `DomainError` is handled. An unexpected exception may have left the
    session inconsistent, so that one is allowed to roll back.
    """
    try:
        yield
    except DomainError:
        await db.commit()
        raise


def _token_response(session: AuthenticatedSession) -> TokenResponse:
    return TokenResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_at=session.expires_at,
        must_change_password=session.user.must_change_password,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DbSession,
    audit: AuditDep,
) -> TokenResponse:
    """Exchange credentials for an access and refresh token pair."""
    settings = get_settings()
    # Keyed on address *and* submitted email so that one attacker cannot lock
    # out an account by exhausting its budget from elsewhere.
    await get_limiter().check(f"login:{client_ip(request)}", settings.rate_limit_auth_per_minute)
    await get_limiter().check(f"login-email:{payload.email}", settings.rate_limit_auth_per_minute)

    service = IdentityService(db, audit)
    async with persist_security_side_effects(db):
        result = await service.authenticate(
            email=payload.email,
            password=payload.password,
            organization_id=payload.organization_id,
            user_agent=request.headers.get("user-agent"),
            ip_address=client_ip(request),
        )
    await db.commit()
    return _token_response(result)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: DbSession,
    audit: AuditDep,
) -> TokenResponse:
    """Rotate a refresh token. The presented token is consumed."""
    settings = get_settings()
    await get_limiter().check(
        f"refresh:{client_ip(request)}", settings.rate_limit_auth_per_minute * 3
    )
    service = IdentityService(db, audit)
    # A replayed token revokes the whole chain before raising; that revocation
    # has to survive the rejection, or the defence does nothing.
    async with persist_security_side_effects(db):
        result = await service.refresh(
            refresh_token=payload.refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip_address=client_ip(request),
        )
    await db.commit()
    return _token_response(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: DbSession, audit: AuditDep) -> None:
    """End the session the refresh token belongs to. Idempotent."""
    service = IdentityService(db, audit)
    await service.logout(refresh_token=payload.refresh_token)
    await db.commit()


@router.get("/me", response_model=CurrentUserResponse)
async def me(context: Ctx, db: DbSession, audit: AuditDep) -> CurrentUserResponse:
    """The signed-in user, their organizations and resolved permissions.

    The frontend calls this on load and uses `permissions` to decide what to
    render. It mirrors the backend's decision; it does not make it.
    """
    service = IdentityService(db, audit)
    user = await service.get_user(context.user_id)
    memberships = await service.list_memberships(user.id)

    summaries: list[MembershipSummary] = []
    active: MembershipSummary | None = None
    for membership in memberships:
        organization = await db.get(Organization, membership.organization_id)
        if organization is None:  # pragma: no cover - FK guarantees presence
            continue
        summary = MembershipSummary(
            id=membership.id,
            organization_id=membership.organization_id,
            organization_name=organization.short_name or organization.legal_name,
            organization_code=organization.code,
            job_title=membership.job_title,
            is_primary_owner=membership.is_primary_owner,
            role_codes=await service.role_codes(membership),
        )
        summaries.append(summary)
        if membership.id == context.membership_id:
            active = summary

    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        locale=user.locale,
        is_superuser=user.is_superuser,
        must_change_password=user.must_change_password,
        active_membership=active,
        memberships=summaries,
        permissions=sorted(context.permissions),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    context: Ctx,
    db: DbSession,
    audit: AuditDep,
) -> None:
    """Change your own password. Ends every other session you have open."""
    service = IdentityService(db, audit)
    user = await service.get_user(context.user_id)
    async with persist_security_side_effects(db):
        await service.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    await db.commit()


@router.post("/switch-organization", response_model=TokenResponse)
async def switch_organization(
    payload: SwitchOrganizationRequest,
    request: Request,
    context: Ctx,
    db: DbSession,
    audit: AuditDep,
) -> TokenResponse:
    """Move the session to another organization you are a member of.

    Requires the current refresh token, which is consumed: the new tokens
    replace the old session rather than adding a second one.
    """
    service = IdentityService(db, audit)
    user = await service.get_user(context.user_id)
    result = await service.switch_organization(
        user=user,
        organization_id=payload.organization_id,
        refresh_token=payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    await db.commit()
    return _token_response(result)
