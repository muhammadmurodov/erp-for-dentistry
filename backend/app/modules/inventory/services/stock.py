"""The stock ledger: what moved, what is left, and what must not go below zero.

**A balance is a sum, never a column.** Every figure this service returns is
computed from `stock_movement` at the moment it is asked for. That is the
whole design: a clinic counts its shelves often enough that a stored total's
drift gets found, usually by somebody blaming the person who counted.

**Stock cannot go negative without authority.** A consumption, a write-off
or a transfer that would take more off a shelf than is on it is refused, and
the refusal names what is actually there. The authorised way to disagree
with the ledger is a physical count, approved by somebody who did not do the
counting — which is what `StockAdjustmentService` is for.

**Nothing here posts to the ledger.** Materials reach the books through the
supplier invoice that bought them. Costing a consumption needs the valuation
method nobody has declared (`OPEN_QUESTIONS.md` I1), so a consumption moves
a quantity and posts nothing at all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.inventory.enums import (
    OPEN_ADJUSTMENT_STATUSES,
    AdjustmentStatus,
    StockMovementKind,
    StockMovementSource,
)
from app.modules.inventory.models import (
    InventoryItem,
    StockAdjustment,
    StockAdjustmentLine,
    StockBatch,
    StockMovement,
)
from app.modules.inventory.services.catalogue import CatalogueService


@dataclass(frozen=True, slots=True)
class StockBalance:
    """What is on one shelf, of one item, in one lot."""

    item_id: uuid.UUID
    item_code: str
    item_name: str
    unit: str
    location_id: uuid.UUID
    batch_id: uuid.UUID | None
    batch_code: str | None
    expires_on: date | None
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class LowStockRow:
    """An item at or below the level somebody said was low."""

    item_id: uuid.UUID
    code: str
    name: str
    unit: str
    on_hand: Decimal
    reorder_level: Decimal


@dataclass(frozen=True, slots=True)
class ExpiringRow:
    """A lot with stock left on it and a date on the box."""

    item_id: uuid.UUID
    item_name: str
    unit: str
    batch_id: uuid.UUID
    batch_code: str
    expires_on: date
    quantity: Decimal
    days_remaining: int


class StockService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._catalogue = CatalogueService(session, audit)

    # --- reading -----------------------------------------------------------

    async def balances(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID | None = None,
        location_id: uuid.UUID | None = None,
        include_empty: bool = False,
    ) -> list[StockBalance]:
        """Every shelf with something on it, summed from the movements."""
        total = func.sum(StockMovement.quantity).label("quantity")
        stmt = (
            select(
                StockMovement.item_id,
                InventoryItem.code,
                InventoryItem.name,
                InventoryItem.unit,
                StockMovement.location_id,
                StockMovement.batch_id,
                StockBatch.code,
                StockBatch.expires_on,
                total,
            )
            .join(InventoryItem, InventoryItem.id == StockMovement.item_id)
            .join(StockBatch, StockBatch.id == StockMovement.batch_id, isouter=True)
            .where(StockMovement.organization_id == organization_id)
            .group_by(
                StockMovement.item_id,
                InventoryItem.code,
                InventoryItem.name,
                InventoryItem.unit,
                StockMovement.location_id,
                StockMovement.batch_id,
                StockBatch.code,
                StockBatch.expires_on,
            )
            .order_by(InventoryItem.name)
        )
        if item_id is not None:
            stmt = stmt.where(StockMovement.item_id == item_id)
        if location_id is not None:
            stmt = stmt.where(StockMovement.location_id == location_id)
        if not include_empty:
            stmt = stmt.having(total != 0)
        rows = (await self._session.execute(stmt)).all()
        return [
            StockBalance(
                item_id=row[0],
                item_code=row[1],
                item_name=row[2],
                unit=row[3],
                location_id=row[4],
                batch_id=row[5],
                batch_code=row[6],
                expires_on=row[7],
                quantity=row[8] or ZERO,
            )
            for row in rows
        ]

    async def on_hand(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        location_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
    ) -> Decimal:
        """How much of one thing is in one place. The authority for a refusal."""
        stmt = select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.organization_id == organization_id,
            StockMovement.item_id == item_id,
        )
        if location_id is not None:
            stmt = stmt.where(StockMovement.location_id == location_id)
        if batch_id is not None:
            stmt = stmt.where(StockMovement.batch_id == batch_id)
        return Decimal((await self._session.execute(stmt)).scalar_one())

    async def low_stock(self, *, organization_id: uuid.UUID) -> list[LowStockRow]:
        """What to buy. Only items whose level somebody actually set.

        An item with no reorder level is never reported: a made-up threshold
        is worse than none, because it trains people to ignore the list.
        """
        total = func.coalesce(func.sum(StockMovement.quantity), 0).label("on_hand")
        stmt = (
            select(
                InventoryItem.id,
                InventoryItem.code,
                InventoryItem.name,
                InventoryItem.unit,
                InventoryItem.reorder_level,
                total,
            )
            .join(
                StockMovement,
                (StockMovement.item_id == InventoryItem.id)
                & (StockMovement.organization_id == InventoryItem.organization_id),
                isouter=True,
            )
            .where(
                InventoryItem.organization_id == organization_id,
                InventoryItem.is_active.is_(True),
                InventoryItem.reorder_level.is_not(None),
            )
            .group_by(
                InventoryItem.id,
                InventoryItem.code,
                InventoryItem.name,
                InventoryItem.unit,
                InventoryItem.reorder_level,
            )
            .having(total <= InventoryItem.reorder_level)
            .order_by(InventoryItem.name)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            LowStockRow(
                item_id=row[0],
                code=row[1],
                name=row[2],
                unit=row[3],
                reorder_level=row[4],
                on_hand=Decimal(row[5] or 0),
            )
            for row in rows
        ]

    async def expiring(
        self, *, organization_id: uuid.UUID, within_days: int = 90, on: date | None = None
    ) -> list[ExpiringRow]:
        """Lots that are out of date or about to be, with stock still on them.

        A lot that has been used up is not reported however old it is: the
        question this answers is what is on the shelf now.
        """
        today = on or clinic_today()
        total = func.coalesce(func.sum(StockMovement.quantity), 0).label("quantity")
        stmt = (
            select(
                StockBatch.item_id,
                InventoryItem.name,
                InventoryItem.unit,
                StockBatch.id,
                StockBatch.code,
                StockBatch.expires_on,
                total,
            )
            .join(InventoryItem, InventoryItem.id == StockBatch.item_id)
            .join(
                StockMovement,
                (StockMovement.batch_id == StockBatch.id)
                & (StockMovement.organization_id == StockBatch.organization_id),
                isouter=True,
            )
            .where(
                StockBatch.organization_id == organization_id,
                StockBatch.expires_on.is_not(None),
            )
            .group_by(
                StockBatch.item_id,
                InventoryItem.name,
                InventoryItem.unit,
                StockBatch.id,
                StockBatch.code,
                StockBatch.expires_on,
            )
            .having(total > 0)
            .order_by(StockBatch.expires_on)
        )
        rows = (await self._session.execute(stmt)).all()
        out: list[ExpiringRow] = []
        for row in rows:
            expires_on: date = row[5]
            days = (expires_on - today).days
            if days > within_days:
                continue
            out.append(
                ExpiringRow(
                    item_id=row[0],
                    item_name=row[1],
                    unit=row[2],
                    batch_id=row[3],
                    batch_code=row[4],
                    expires_on=expires_on,
                    quantity=Decimal(row[6] or 0),
                    days_remaining=days,
                )
            )
        return out

    async def movements(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID | None = None,
        location_id: uuid.UUID | None = None,
        kind: StockMovementKind | None = None,
        limit: int = 200,
    ) -> list[StockMovement]:
        stmt = select(StockMovement).where(StockMovement.organization_id == organization_id)
        if item_id is not None:
            stmt = stmt.where(StockMovement.item_id == item_id)
        if location_id is not None:
            stmt = stmt.where(StockMovement.location_id == location_id)
        if kind is not None:
            stmt = stmt.where(StockMovement.kind == kind)
        stmt = stmt.order_by(StockMovement.occurred_on.desc(), StockMovement.recorded_at.desc())
        return list((await self._session.execute(stmt.limit(limit))).scalars().all())

    # --- writing -----------------------------------------------------------

    async def record(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        location_id: uuid.UUID,
        kind: StockMovementKind,
        quantity: Decimal,
        source_kind: StockMovementSource,
        source_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        occurred_on: date | None = None,
        reason: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockMovement:
        """Write one movement, after checking it leaves no shelf short.

        The signed quantity is the caller's: `+5` puts five on the shelf,
        `-5` takes five off. The check and the write happen under an advisory
        lock on the item and the location, because two people recording a
        consumption at the same moment would otherwise both read the balance
        before either had written — and the second would be allowed to take
        stock that the first had already taken.
        """
        if quantity == ZERO:
            raise ValidationFailed("A movement of nothing is not a movement")
        item = await self._catalogue.get_item(organization_id=organization_id, item_id=item_id)
        await self._catalogue.get_location(organization_id=organization_id, location_id=location_id)
        if item.is_batch_tracked and batch_id is None:
            raise ValidationFailed(
                f"{item.name} is tracked by batch, so this has to say which lot",
                details={"item_id": str(item_id)},
            )
        if batch_id is not None:
            batch = await self._catalogue.get_batch(
                organization_id=organization_id, batch_id=batch_id
            )
            if batch.item_id != item_id:
                raise ValidationFailed(
                    "That batch belongs to a different material",
                    details={"batch_id": str(batch_id)},
                )

        await self._lock(organization_id=organization_id, item_id=item_id, location_id=location_id)

        if quantity < ZERO:
            available = await self.on_hand(
                organization_id=organization_id,
                item_id=item_id,
                location_id=location_id,
                batch_id=batch_id,
            )
            if available + quantity < ZERO:
                raise Conflict(
                    f"There is not that much {item.name} on the shelf: "
                    f"{available} {item.unit} left, and this would take "
                    f"{abs(quantity)}. Count the shelf and have the difference "
                    "approved if the figure is wrong.",
                    details={
                        "item_id": str(item_id),
                        "available": str(available),
                        "requested": str(abs(quantity)),
                    },
                )

        movement = StockMovement(
            organization_id=organization_id,
            item_id=item_id,
            location_id=location_id,
            batch_id=batch_id,
            kind=kind,
            quantity=quantity,
            occurred_on=occurred_on or clinic_today(),
            source_kind=source_kind,
            source_id=source_id,
            reason=(reason or "").strip() or None,
            recorded_by_user_id=actor_user_id,
            recorded_at=datetime.now(UTC),
        )
        self._session.add(movement)
        await self._session.flush()
        return movement

    async def _lock(
        self, *, organization_id: uuid.UUID, item_id: uuid.UUID, location_id: uuid.UUID
    ) -> None:
        """Serialise everybody moving this item on this shelf.

        The same shape as the appointment book's lock: the conflict is with a
        row the other transaction has not committed, so there is nothing to
        lock except the pair of identifiers itself.
        """
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"stock:{organization_id}:{item_id}:{location_id}", 0)
                )
            )
        )

    async def receive(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        location_id: uuid.UUID,
        quantity: Decimal,
        receipt_line_id: uuid.UUID,
        batch_id: uuid.UUID | None = None,
        occurred_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockMovement:
        """Put a delivery on a shelf. Called by purchasing, never by hand.

        Stock arrives through the door the money came through: the only way
        to create a `RECEIPT` is to record the goods receipt that justifies
        it, so what is on the shelf can always be traced to a delivery note.
        """
        if quantity <= ZERO:
            raise ValidationFailed("A delivery has to be a positive quantity")
        movement = await self.record(
            organization_id=organization_id,
            item_id=item_id,
            location_id=location_id,
            kind=StockMovementKind.RECEIPT,
            quantity=quantity,
            source_kind=StockMovementSource.GOODS_RECEIPT_LINE,
            source_id=receipt_line_id,
            batch_id=batch_id,
            occurred_on=occurred_on,
            actor_user_id=actor_user_id,
        )
        self._audit.record(
            events.STOCK_RECEIVED,
            organization_id=organization_id,
            target_type="stock_movement",
            target_id=movement.id,
            target_label=str(item_id),
            actor_user_id=actor_user_id,
            new_values={"quantity": str(quantity), "receipt_line": str(receipt_line_id)},
        )
        return movement

    async def consume(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        quantity: Decimal,
        location_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        treatment_item_id: uuid.UUID | None = None,
        occurred_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockMovement:
        """Record material used up. **Posts nothing.**

        What the material cost is already in the books — it was debited to
        1010 when the supplier invoice was registered. Moving that cost to an
        expense as it is used needs the valuation method nobody has declared
        (`OPEN_QUESTIONS.md` I1), so this records the quantity and leaves the
        ledger alone rather than guessing an amount.
        """
        if quantity <= ZERO:
            raise ValidationFailed("A consumed quantity has to be positive")
        if location_id is None:
            location_id = (
                await self._catalogue.default_location(organization_id=organization_id)
            ).id
        movement = await self.record(
            organization_id=organization_id,
            item_id=item_id,
            location_id=location_id,
            kind=StockMovementKind.CONSUMPTION,
            quantity=-quantity,
            source_kind=(
                StockMovementSource.TREATMENT_ITEM
                if treatment_item_id is not None
                else StockMovementSource.MANUAL
            ),
            source_id=treatment_item_id,
            batch_id=batch_id,
            occurred_on=occurred_on,
            actor_user_id=actor_user_id,
        )
        self._audit.record(
            events.STOCK_CONSUMED,
            organization_id=organization_id,
            target_type="stock_movement",
            target_id=movement.id,
            target_label=str(item_id),
            actor_user_id=actor_user_id,
            new_values={
                "quantity": str(quantity),
                "treatment_item": str(treatment_item_id) if treatment_item_id else None,
            },
        )
        return movement

    async def write_off(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        quantity: Decimal,
        reason: str,
        location_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        occurred_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockMovement:
        """Take expired or broken stock off, with a reason, always audited."""
        if quantity <= ZERO:
            raise ValidationFailed("A written-off quantity has to be positive")
        if not (reason or "").strip():
            raise ValidationFailed("Stock written off has to say why")
        if location_id is None:
            location_id = (
                await self._catalogue.default_location(organization_id=organization_id)
            ).id
        movement = await self.record(
            organization_id=organization_id,
            item_id=item_id,
            location_id=location_id,
            kind=StockMovementKind.WRITE_OFF,
            quantity=-quantity,
            source_kind=StockMovementSource.MANUAL,
            batch_id=batch_id,
            occurred_on=occurred_on,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        self._audit.record(
            events.STOCK_WRITTEN_OFF,
            organization_id=organization_id,
            target_type="stock_movement",
            target_id=movement.id,
            target_label=str(item_id),
            actor_user_id=actor_user_id,
            new_values={"quantity": str(quantity), "reason": reason.strip()},
        )
        return movement

    async def transfer(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        from_location_id: uuid.UUID,
        to_location_id: uuid.UUID,
        quantity: Decimal,
        batch_id: uuid.UUID | None = None,
        occurred_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> tuple[StockMovement, StockMovement]:
        """Move stock between shelves as two movements that balance to zero.

        Two rows rather than one with two locations, so that every balance is
        the same sum over the same column and a transfer can never change the
        total the clinic owns.
        """
        if from_location_id == to_location_id:
            raise ValidationFailed("A transfer needs two different locations")
        if quantity <= ZERO:
            raise ValidationFailed("A transferred quantity has to be positive")
        out = await self.record(
            organization_id=organization_id,
            item_id=item_id,
            location_id=from_location_id,
            kind=StockMovementKind.TRANSFER_OUT,
            quantity=-quantity,
            source_kind=StockMovementSource.STOCK_TRANSFER,
            batch_id=batch_id,
            occurred_on=occurred_on,
            actor_user_id=actor_user_id,
        )
        into = await self.record(
            organization_id=organization_id,
            item_id=item_id,
            location_id=to_location_id,
            kind=StockMovementKind.TRANSFER_IN,
            quantity=quantity,
            source_kind=StockMovementSource.STOCK_TRANSFER,
            source_id=out.id,
            batch_id=batch_id,
            occurred_on=occurred_on,
            actor_user_id=actor_user_id,
        )
        out.source_id = into.id
        await self._session.flush()
        self._audit.record(
            events.STOCK_TRANSFERRED,
            organization_id=organization_id,
            target_type="stock_movement",
            target_id=into.id,
            target_label=str(item_id),
            actor_user_id=actor_user_id,
            new_values={
                "quantity": str(quantity),
                "from": str(from_location_id),
                "to": str(to_location_id),
            },
        )
        return out, into


class StockAdjustmentService:
    """A physical count: proposed by whoever counted, approved by somebody else.

    Nothing moves until approval. The two halves are held apart by
    `inventory.adjustment.approve`, which the inventory manager deliberately
    does not hold — the person who counts the shelf is not the person who
    decides the shelf was wrong.
    """

    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._catalogue = CatalogueService(session, audit)
        self._stock = StockService(session, audit)

    async def list_adjustments(
        self, *, organization_id: uuid.UUID, status: AdjustmentStatus | None = None
    ) -> list[StockAdjustment]:
        stmt = select(StockAdjustment).where(StockAdjustment.organization_id == organization_id)
        if status is not None:
            stmt = stmt.where(StockAdjustment.status == status)
        return list(
            (await self._session.execute(stmt.order_by(StockAdjustment.counted_on.desc())))
            .scalars()
            .all()
        )

    async def get(self, *, organization_id: uuid.UUID, adjustment_id: uuid.UUID) -> StockAdjustment:
        stmt = select(StockAdjustment).where(
            StockAdjustment.organization_id == organization_id,
            StockAdjustment.id == adjustment_id,
        )
        adjustment = (await self._session.execute(stmt)).scalar_one_or_none()
        if adjustment is None:
            raise NotFound("Stock count not found")
        return adjustment

    async def lines(
        self, *, organization_id: uuid.UUID, adjustment_id: uuid.UUID
    ) -> list[StockAdjustmentLine]:
        stmt = (
            select(StockAdjustmentLine)
            .where(
                StockAdjustmentLine.organization_id == organization_id,
                StockAdjustmentLine.adjustment_id == adjustment_id,
            )
            .order_by(StockAdjustmentLine.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        location_id: uuid.UUID,
        reason: str,
        counted: list[tuple[uuid.UUID, uuid.UUID | None, Decimal, str | None]],
        counted_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockAdjustment:
        """Record a count. Moves nothing.

        Each line keeps what the system believed at the moment of counting,
        so the paper can be reconciled months later — but approval writes a
        movement for the difference *recomputed then*, not for this snapshot,
        because stock may legitimately move between counting and approving.
        """
        if not (reason or "").strip():
            raise ValidationFailed("A stock count has to say why it was done")
        if not counted:
            raise ValidationFailed("A stock count needs at least one line")
        await self._catalogue.get_location(organization_id=organization_id, location_id=location_id)
        adjustment = StockAdjustment(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="stock_adjustment",
                prefix="SC-",
                padding=5,
            ),
            location_id=location_id,
            counted_on=counted_on or clinic_today(),
            status=AdjustmentStatus.DRAFT,
            reason=reason.strip(),
            counted_by_user_id=actor_user_id,
        )
        self._session.add(adjustment)
        await self._session.flush()

        seen: set[tuple[uuid.UUID, uuid.UUID | None]] = set()
        for item_id, batch_id, quantity, note in counted:
            if quantity < ZERO:
                raise ValidationFailed("A counted quantity cannot be negative")
            if (item_id, batch_id) in seen:
                raise ValidationFailed("The same material is counted twice on this sheet")
            seen.add((item_id, batch_id))
            await self._catalogue.get_item(organization_id=organization_id, item_id=item_id)
            system = await self._stock.on_hand(
                organization_id=organization_id,
                item_id=item_id,
                location_id=location_id,
                batch_id=batch_id,
            )
            self._session.add(
                StockAdjustmentLine(
                    organization_id=organization_id,
                    adjustment_id=adjustment.id,
                    item_id=item_id,
                    batch_id=batch_id,
                    system_quantity=system,
                    counted_quantity=quantity,
                    note=(note or "").strip() or None,
                )
            )
        await self._session.flush()
        return adjustment

    async def propose(
        self,
        *,
        organization_id: uuid.UUID,
        adjustment_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockAdjustment:
        adjustment = await self.get(organization_id=organization_id, adjustment_id=adjustment_id)
        if adjustment.status is not AdjustmentStatus.DRAFT:
            raise Conflict(
                f"This count is {adjustment.status.value.lower()} and cannot be proposed again",
                details={"status": adjustment.status.value},
            )
        adjustment.status = AdjustmentStatus.PROPOSED
        await self._session.flush()
        self._audit.record(
            events.STOCK_COUNT_PROPOSED,
            organization_id=organization_id,
            target_type="stock_adjustment",
            target_id=adjustment.id,
            target_label=adjustment.code,
            actor_user_id=actor_user_id,
            new_values={"status": adjustment.status.value},
        )
        return adjustment

    async def approve(
        self,
        *,
        organization_id: uuid.UUID,
        adjustment_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockAdjustment:
        """Approve a count: this is the moment stock moves.

        The approver may not be the person who counted. That is the whole
        point of the control, and it is checked here rather than only in the
        permission, because holding both permissions is possible in a clinic
        of three and still should not let one person do both halves.
        """
        adjustment = await self.get(organization_id=organization_id, adjustment_id=adjustment_id)
        if adjustment.status is not AdjustmentStatus.PROPOSED:
            raise Conflict(
                f"Only a proposed count can be approved; this one is "
                f"{adjustment.status.value.lower()}",
                details={"status": adjustment.status.value},
            )
        if (
            actor_user_id is not None
            and adjustment.counted_by_user_id is not None
            and actor_user_id == adjustment.counted_by_user_id
        ):
            raise Conflict(
                "The person who counted the stock cannot approve their own "
                "count. Somebody else has to agree the difference.",
                details={"control": "inventory.adjustment.approve"},
            )

        lines = await self.lines(organization_id=organization_id, adjustment_id=adjustment_id)
        moved = 0
        for line in lines:
            current = await self._stock.on_hand(
                organization_id=organization_id,
                item_id=line.item_id,
                location_id=adjustment.location_id,
                batch_id=line.batch_id,
            )
            difference = line.counted_quantity - current
            if difference == ZERO:
                continue
            await self._stock.record(
                organization_id=organization_id,
                item_id=line.item_id,
                location_id=adjustment.location_id,
                kind=StockMovementKind.ADJUSTMENT,
                quantity=difference,
                source_kind=StockMovementSource.STOCK_ADJUSTMENT,
                source_id=adjustment.id,
                batch_id=line.batch_id,
                occurred_on=adjustment.counted_on,
                reason=f"{adjustment.code}: {adjustment.reason}",
                actor_user_id=actor_user_id,
            )
            moved += 1

        adjustment.status = AdjustmentStatus.APPROVED
        adjustment.decided_by_user_id = actor_user_id
        adjustment.decided_at = datetime.now(UTC)
        await self._session.flush()
        self._audit.record(
            events.STOCK_ADJUSTED,
            organization_id=organization_id,
            target_type="stock_adjustment",
            target_id=adjustment.id,
            target_label=adjustment.code,
            business_event=f"stock_adjustment:{adjustment.id}",
            actor_user_id=actor_user_id,
            new_values={
                "status": adjustment.status.value,
                "lines_moved": moved,
                "reason": adjustment.reason,
            },
        )
        return adjustment

    async def reject(
        self,
        *,
        organization_id: uuid.UUID,
        adjustment_id: uuid.UUID,
        note: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockAdjustment:
        adjustment = await self.get(organization_id=organization_id, adjustment_id=adjustment_id)
        if adjustment.status not in OPEN_ADJUSTMENT_STATUSES:
            raise Conflict(
                f"This count is already {adjustment.status.value.lower()}",
                details={"status": adjustment.status.value},
            )
        if not (note or "").strip():
            raise ValidationFailed("A refused count has to say why")
        adjustment.status = AdjustmentStatus.REJECTED
        adjustment.decided_by_user_id = actor_user_id
        adjustment.decided_at = datetime.now(UTC)
        adjustment.decision_note = note.strip()
        await self._session.flush()
        self._audit.record(
            events.STOCK_COUNT_REJECTED,
            organization_id=organization_id,
            target_type="stock_adjustment",
            target_id=adjustment.id,
            target_label=adjustment.code,
            actor_user_id=actor_user_id,
            new_values={"status": adjustment.status.value, "note": note.strip()},
        )
        return adjustment
