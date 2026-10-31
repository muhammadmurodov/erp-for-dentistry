"""Organization profile and currency reference endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.modules.organization.schemas import (
    CurrencyResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
)
from app.modules.organization.service import OrganizationService

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get(
    "",
    response_model=OrganizationResponse,
    dependencies=[Depends(require_permissions("organization.read"))],
)
async def get_current_organization(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> OrganizationResponse:
    """The organization this session is signed in to.

    The id comes from the access token, never from a path or query parameter,
    so there is no identifier for a caller to substitute.
    """
    service = OrganizationService(db, audit)
    organization = await service.get(context.require_organization())
    return OrganizationResponse.model_validate(organization)


@router.patch(
    "",
    response_model=OrganizationResponse,
    dependencies=[Depends(require_permissions("organization.manage"))],
)
async def update_current_organization(
    payload: OrganizationUpdateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> OrganizationResponse:
    """Edit the organization profile. Every changed field is audited."""
    service = OrganizationService(db, audit)
    organization = await service.update(
        organization_id=context.require_organization(),
        changes=payload.model_dump(exclude_unset=True),
    )
    await db.commit()
    return OrganizationResponse.model_validate(organization)


@router.get("/currencies", response_model=list[CurrencyResponse])
async def list_currencies(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[CurrencyResponse]:
    """Active currencies and their rounding scale."""
    service = OrganizationService(db, audit)
    return [CurrencyResponse.model_validate(c) for c in await service.list_currencies()]
