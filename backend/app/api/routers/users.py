"""User, membership and role administration inside one organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.errors import NotFound
from app.core.tenancy import scoped
from app.modules.identity.models import Membership, Role, User
from app.modules.identity.schemas import (
    OrganizationUserResponse,
    RoleGrantRequest,
    RoleResponse,
    UserCreateRequest,
)
from app.modules.identity.service import IdentityService

router = APIRouter(tags=["users"])


async def _to_response(
    service: IdentityService, membership: Membership, user: User
) -> OrganizationUserResponse:
    return OrganizationUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        locale=user.locale,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        membership_id=membership.id,
        job_title=membership.job_title,
        membership_active=membership.is_active,
        is_primary_owner=membership.is_primary_owner,
        role_codes=await service.role_codes(membership),
    )


@router.get(
    "/users",
    response_model=list[OrganizationUserResponse],
    dependencies=[Depends(require_permissions("identity.user.read"))],
)
async def list_organization_users(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[OrganizationUserResponse]:
    """People with a membership in the caller's organization."""
    organization_id = context.require_organization()
    stmt = scoped(
        select(Membership).options(selectinload(Membership.roles)),
        Membership,
        organization_id,
    ).order_by(Membership.created_at)
    memberships = list((await db.execute(stmt)).scalars().all())
    service = IdentityService(db, audit)
    return [await _to_response(service, m, await service.get_user(m.user_id)) for m in memberships]


@router.post(
    "/users",
    response_model=OrganizationUserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("identity.user.manage"))],
)
async def create_organization_user(
    payload: UserCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> OrganizationUserResponse:
    """Add a person to this organization, optionally with initial roles.

    If the email already belongs to an account, that account gains a
    membership here rather than being duplicated.
    """
    organization_id = context.require_organization()
    service = IdentityService(db, audit)
    user, membership = await service.create_user_in_organization(
        organization_id=organization_id,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        role_codes=payload.role_codes,
        phone=payload.phone,
        locale=payload.locale,
        job_title=payload.job_title,
    )
    await db.commit()
    return await _to_response(service, membership, user)


async def _load_membership(
    db: DbSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> Membership:
    """Load a membership *within* the caller's organization.

    Scoping the query rather than fetching by primary key and comparing means
    another organization's membership id simply does not resolve, and the 404
    reveals nothing about whether it exists elsewhere.
    """
    stmt = scoped(
        select(Membership).options(selectinload(Membership.roles)),
        Membership,
        organization_id,
    ).where(Membership.id == membership_id)
    membership = (await db.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise NotFound("Member not found")
    return membership


@router.put(
    "/users/{membership_id}/roles",
    response_model=OrganizationUserResponse,
    dependencies=[Depends(require_permissions("identity.role.manage"))],
)
async def set_member_roles(
    membership_id: uuid.UUID,
    payload: RoleGrantRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> OrganizationUserResponse:
    """Replace a member's roles.

    Refuses combinations that break a segregation-of-duties control, even when
    each role is individually valid.
    """
    organization_id = context.require_organization()
    membership = await _load_membership(db, organization_id, membership_id)
    service = IdentityService(db, audit)
    await service.set_membership_roles(membership=membership, role_codes=payload.role_codes)
    await db.commit()
    await db.refresh(membership)
    return await _to_response(service, membership, await service.get_user(membership.user_id))


@router.post(
    "/users/{membership_id}/deactivate",
    response_model=OrganizationUserResponse,
    dependencies=[Depends(require_permissions("identity.user.manage"))],
)
async def deactivate_member(
    membership_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    reason: str | None = None,
) -> OrganizationUserResponse:
    """Revoke a person's access to this organization.

    The user account itself is untouched: they may still be a member elsewhere.
    """
    organization_id = context.require_organization()
    membership = await _load_membership(db, organization_id, membership_id)
    service = IdentityService(db, audit)
    await service.set_membership_active(membership=membership, is_active=False, reason=reason)
    await db.commit()
    return await _to_response(service, membership, await service.get_user(membership.user_id))


@router.get(
    "/roles",
    response_model=list[RoleResponse],
    dependencies=[Depends(require_permissions("identity.role.read"))],
)
async def list_roles(context: OrgCtx, db: DbSession, audit: AuditDep) -> list[RoleResponse]:
    """Roles available in this organization: the system set plus its own."""
    organization_id = context.require_organization()
    stmt = (
        select(Role)
        .where((Role.organization_id == organization_id) | (Role.organization_id.is_(None)))
        .options(selectinload(Role.permissions))
        .order_by(Role.is_system.desc(), Role.code)
    )
    roles = list((await db.execute(stmt)).scalars().all())
    return [
        RoleResponse(
            id=r.id,
            code=r.code,
            name_uz=r.name_uz,
            name_ru=r.name_ru,
            name_en=r.name_en,
            description=r.description,
            is_system=r.is_system,
            is_read_only=r.is_read_only,
            permission_codes=sorted(link.permission_code for link in r.permissions),
        )
        for r in roles
    ]
