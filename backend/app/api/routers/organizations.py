"""Organization profile and currency reference endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.modules.organization.schemas import (
    CurrencyResponse,
    DutySeparationRequest,
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


@router.put(
    "/duty-separation",
    response_model=OrganizationResponse,
    dependencies=[Depends(require_permissions("organization.duties.declare"))],
)
async def declare_duty_separation(
    payload: DutySeparationRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> OrganizationResponse:
    """Declare that this organization has too few people to separate duties.

    The owner's act and nobody else's: `organization.duties.declare` is held
    by the owner role alone, because this is the one setting that decides a
    control applies to the person who runs the till rather than to somebody
    standing behind them. Audited in both directions with a mandatory reason
    (ADR-041).

    It grants no permission. What it removes is the requirement that an
    approver be a *different* person — the condition a two- or three-person
    clinic cannot satisfy — and every act that then goes through on one pair
    of hands is marked `self_approved` where it is recorded.
    """
    service = OrganizationService(db, audit)
    organization = await service.declare_duty_separation(
        organization_id=context.require_organization(),
        cannot_be_separated=payload.duties_cannot_be_separated,
        reason=payload.reason,
        actor_user_id=context.user_id,
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
