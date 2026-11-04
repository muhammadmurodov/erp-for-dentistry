"""Materials, shelves, batches, the stock ledger and the physical count.

**Nothing here posts to the ledger**, and that is the module's central
decision rather than an omission. What materials cost is already in the
books — debited to 1010 when the supplier invoice was registered. Moving
that cost to an expense as material is used needs the valuation method
nobody has declared (`OPEN_QUESTIONS.md` I1), so a consumption records a
quantity and leaves the accounts alone.

Three authority boundaries, which are why the endpoints are split as they
are:

* **Reading stock** (`inventory.item.read`) is separate from **editing the
  catalogue** (`inventory.item.manage`). The dentist reads; the person who
  buys maintains.
* **Recording what was used** (`inventory.consumption.record`) is the
  dentist's, and nothing else in this module is.
* **Counting a shelf** and **approving the difference** are two acts.
  Proposing a count needs `inventory.item.manage`; approving it needs
  `inventory.adjustment.approve`, which the inventory manager deliberately
  does not hold — and the service refuses a self-approval even when one
  person holds both, because a clinic of three will.

There is no endpoint that creates a `RECEIPT`. Stock arrives through the
goods receipt that justifies it, so every quantity on a shelf can be traced
back to a delivery note.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.modules.inventory.enums import AdjustmentStatus, StockMovementKind
from app.modules.inventory.schemas import (
    AdjustmentCreateRequest,
    AdjustmentDecisionRequest,
    AdjustmentDetailResponse,
    AdjustmentLineResponse,
    AdjustmentResponse,
    BalanceRow,
    BatchCreateRequest,
    BatchResponse,
    CategoryCreateRequest,
    CategoryResponse,
    ConsumptionRequest,
    ExpiringResponse,
    ItemCreateRequest,
    ItemResponse,
    ItemUpdateRequest,
    LocationCreateRequest,
    LocationResponse,
    LowStockResponse,
    MovementResponse,
    TransferRequest,
    ValuationResponse,
    ValuationRow,
    WriteOffRequest,
)
from app.modules.inventory.services.catalogue import CatalogueService
from app.modules.inventory.services.stock import StockAdjustmentService, StockService
from app.modules.purchasing.services.stock_valuation import StockValuationService

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ---------------------------------------------------------------- categories


@router.get(
    "/categories",
    response_model=list[CategoryResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def list_categories(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[CategoryResponse]:
    rows = await CatalogueService(db, audit).list_categories(
        organization_id=context.require_organization()
    )
    return [CategoryResponse.model_validate(row) for row in rows]


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def create_category(
    payload: CategoryCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> CategoryResponse:
    category = await CatalogueService(db, audit).create_category(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return CategoryResponse.model_validate(category)


# ----------------------------------------------------------------- locations


@router.get(
    "/locations",
    response_model=list[LocationResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def list_locations(context: OrgCtx, db: DbSession, audit: AuditDep) -> list[LocationResponse]:
    rows = await CatalogueService(db, audit).list_locations(
        organization_id=context.require_organization()
    )
    return [LocationResponse.model_validate(row) for row in rows]


@router.post(
    "/locations",
    response_model=LocationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def create_location(
    payload: LocationCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> LocationResponse:
    location = await CatalogueService(db, audit).create_location(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        is_default=payload.is_default,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return LocationResponse.model_validate(location)


# --------------------------------------------------------------------- items


@router.get(
    "/items",
    response_model=list[ItemResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def list_items(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    search: str | None = Query(default=None),
    category_id: uuid.UUID | None = Query(default=None),
    include_inactive: bool = Query(default=False),
) -> list[ItemResponse]:
    rows = await CatalogueService(db, audit).list_items(
        organization_id=context.require_organization(),
        search=search,
        category_id=category_id,
        active_only=not include_inactive,
    )
    return [ItemResponse.model_validate(row) for row in rows]


@router.get(
    "/items/{item_id}",
    response_model=ItemResponse,
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def get_item(
    item_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> ItemResponse:
    item = await CatalogueService(db, audit).get_item(
        organization_id=context.require_organization(), item_id=item_id
    )
    return ItemResponse.model_validate(item)


@router.post(
    "/items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def create_item(
    payload: ItemCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> ItemResponse:
    item = await CatalogueService(db, audit).create_item(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        unit=payload.unit,
        category_id=payload.category_id,
        reorder_level=payload.reorder_level,
        is_batch_tracked=payload.is_batch_tracked,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return ItemResponse.model_validate(item)


@router.patch(
    "/items/{item_id}",
    response_model=ItemResponse,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> ItemResponse:
    item = await CatalogueService(db, audit).update_item(
        organization_id=context.require_organization(),
        item_id=item_id,
        actor_user_id=context.user_id,
        **payload.model_dump(exclude_unset=True),
    )
    await db.commit()
    return ItemResponse.model_validate(item)


# ------------------------------------------------------------------- batches


@router.get(
    "/batches",
    response_model=list[BatchResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def list_batches(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    item_id: uuid.UUID | None = Query(default=None),
) -> list[BatchResponse]:
    rows = await CatalogueService(db, audit).list_batches(
        organization_id=context.require_organization(), item_id=item_id
    )
    return [BatchResponse.model_validate(row) for row in rows]


@router.post(
    "/batches",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def create_batch(
    payload: BatchCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> BatchResponse:
    batch = await CatalogueService(db, audit).ensure_batch(
        organization_id=context.require_organization(),
        item_id=payload.item_id,
        code=payload.code,
        expires_on=payload.expires_on,
    )
    await db.commit()
    return BatchResponse.model_validate(batch)


# ------------------------------------------------------------------ the shelf


@router.get(
    "/stock",
    response_model=list[BalanceRow],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def stock_on_hand(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    item_id: uuid.UUID | None = Query(default=None),
    location_id: uuid.UUID | None = Query(default=None),
) -> list[BalanceRow]:
    """What is on the shelves, summed from the movements every time."""
    rows = await StockService(db, audit).balances(
        organization_id=context.require_organization(),
        item_id=item_id,
        location_id=location_id,
    )
    return [BalanceRow(**asdict(row)) for row in rows]


@router.get(
    "/stock/low",
    response_model=list[LowStockResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def low_stock(context: OrgCtx, db: DbSession, audit: AuditDep) -> list[LowStockResponse]:
    rows = await StockService(db, audit).low_stock(
        organization_id=context.require_organization()
    )
    return [LowStockResponse(**asdict(row)) for row in rows]


@router.get(
    "/stock/expiring",
    response_model=list[ExpiringResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def expiring_stock(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    within_days: int = Query(default=90, ge=0, le=3650),
) -> list[ExpiringResponse]:
    rows = await StockService(db, audit).expiring(
        organization_id=context.require_organization(), within_days=within_days
    )
    return [ExpiringResponse(**asdict(row)) for row in rows]


@router.get(
    "/movements",
    response_model=list[MovementResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def list_movements(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    item_id: uuid.UUID | None = Query(default=None),
    location_id: uuid.UUID | None = Query(default=None),
    kind: StockMovementKind | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[MovementResponse]:
    rows = await StockService(db, audit).movements(
        organization_id=context.require_organization(),
        item_id=item_id,
        location_id=location_id,
        kind=kind,
        limit=limit,
    )
    return [MovementResponse.model_validate(row) for row in rows]


@router.post(
    "/consumption",
    response_model=MovementResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.consumption.record"))],
)
async def record_consumption(
    payload: ConsumptionRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> MovementResponse:
    """Record material used up. Posts nothing; see the module docstring."""
    movement = await StockService(db, audit).consume(
        organization_id=context.require_organization(),
        item_id=payload.item_id,
        quantity=payload.quantity,
        location_id=payload.location_id,
        batch_id=payload.batch_id,
        treatment_item_id=payload.treatment_item_id,
        occurred_on=payload.occurred_on,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return MovementResponse.model_validate(movement)


@router.post(
    "/write-offs",
    response_model=MovementResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.adjustment.approve"))],
)
async def write_off(
    payload: WriteOffRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> MovementResponse:
    """Take expired or broken stock off the shelf, with a reason.

    Behind the approval permission rather than the recording one: a
    write-off destroys value on one person's say-so, which is the same shape
    as approving a cash discrepancy, and it is audited for the same reason.
    """
    movement = await StockService(db, audit).write_off(
        organization_id=context.require_organization(),
        item_id=payload.item_id,
        quantity=payload.quantity,
        reason=payload.reason,
        location_id=payload.location_id,
        batch_id=payload.batch_id,
        occurred_on=payload.occurred_on,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return MovementResponse.model_validate(movement)


@router.post(
    "/transfers",
    response_model=list[MovementResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def transfer_stock(
    payload: TransferRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[MovementResponse]:
    out, into = await StockService(db, audit).transfer(
        organization_id=context.require_organization(),
        item_id=payload.item_id,
        from_location_id=payload.from_location_id,
        to_location_id=payload.to_location_id,
        quantity=payload.quantity,
        batch_id=payload.batch_id,
        occurred_on=payload.occurred_on,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return [MovementResponse.model_validate(out), MovementResponse.model_validate(into)]


# ---------------------------------------------------------------- the count


@router.get(
    "/counts",
    response_model=list[AdjustmentResponse],
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def list_counts(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    status_filter: AdjustmentStatus | None = Query(default=None, alias="status"),
) -> list[AdjustmentResponse]:
    rows = await StockAdjustmentService(db, audit).list_adjustments(
        organization_id=context.require_organization(), status=status_filter
    )
    return [AdjustmentResponse.model_validate(row) for row in rows]


@router.get(
    "/counts/{adjustment_id}",
    response_model=AdjustmentDetailResponse,
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def get_count(
    adjustment_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> AdjustmentDetailResponse:
    service = StockAdjustmentService(db, audit)
    organization_id = context.require_organization()
    adjustment = await service.get(organization_id=organization_id, adjustment_id=adjustment_id)
    lines = await service.lines(organization_id=organization_id, adjustment_id=adjustment_id)
    return AdjustmentDetailResponse(
        **AdjustmentResponse.model_validate(adjustment).model_dump(),
        lines=[AdjustmentLineResponse.model_validate(line) for line in lines],
    )


@router.post(
    "/counts",
    response_model=AdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def create_count(
    payload: AdjustmentCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> AdjustmentResponse:
    """Record a physical count. Moves nothing until it is approved."""
    adjustment = await StockAdjustmentService(db, audit).create(
        organization_id=context.require_organization(),
        location_id=payload.location_id,
        reason=payload.reason,
        counted_on=payload.counted_on,
        counted=[
            (line.item_id, line.batch_id, line.counted_quantity, line.note)
            for line in payload.lines
        ],
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AdjustmentResponse.model_validate(adjustment)


@router.post(
    "/counts/{adjustment_id}/propose",
    response_model=AdjustmentResponse,
    dependencies=[Depends(require_permissions("inventory.item.manage"))],
)
async def propose_count(
    adjustment_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> AdjustmentResponse:
    adjustment = await StockAdjustmentService(db, audit).propose(
        organization_id=context.require_organization(),
        adjustment_id=adjustment_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AdjustmentResponse.model_validate(adjustment)


@router.post(
    "/counts/{adjustment_id}/approve",
    response_model=AdjustmentResponse,
    dependencies=[Depends(require_permissions("inventory.adjustment.approve"))],
)
async def approve_count(
    adjustment_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> AdjustmentResponse:
    """Approve a count: the moment the stock ledger actually changes."""
    adjustment = await StockAdjustmentService(db, audit).approve(
        organization_id=context.require_organization(),
        adjustment_id=adjustment_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AdjustmentResponse.model_validate(adjustment)


@router.post(
    "/counts/{adjustment_id}/reject",
    response_model=AdjustmentResponse,
    dependencies=[Depends(require_permissions("inventory.adjustment.approve"))],
)
async def reject_count(
    adjustment_id: uuid.UUID,
    payload: AdjustmentDecisionRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AdjustmentResponse:
    adjustment = await StockAdjustmentService(db, audit).reject(
        organization_id=context.require_organization(),
        adjustment_id=adjustment_id,
        note=payload.note or "",
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AdjustmentResponse.model_validate(adjustment)


# ------------------------------------------------------------------ valuation


@router.get(
    "/valuation",
    response_model=ValuationResponse,
    dependencies=[Depends(require_permissions("inventory.item.read"))],
)
async def stock_valuation(context: OrgCtx, db: DbSession, audit: AuditDep) -> ValuationResponse:
    """What the stock is worth — or an honest statement that nobody has said.

    While the organization's valuation method is `UNDECLARED` this still
    returns every quantity, with every cost null. The clinic can see what it
    has without the system inventing what it is worth (`OPEN_QUESTIONS` I1).
    """
    valuation = await StockValuationService(db, audit).value(
        organization_id=context.require_organization()
    )
    return ValuationResponse(
        method=valuation.method,
        currency=valuation.currency,
        total_value=valuation.total_value,
        unpriced_items=valuation.unpriced_items,
        rows=[ValuationRow(**asdict(row)) for row in valuation.rows],
    )
