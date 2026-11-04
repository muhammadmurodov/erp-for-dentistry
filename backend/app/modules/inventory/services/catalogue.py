"""The materials themselves: what they are, where they live, which lot.

Nothing here moves stock. This is the reference data a movement points at,
kept deliberately small — a clinic of three will enter perhaps forty items
once and then hardly ever open this screen again.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.inventory.models import (
    InventoryCategory,
    InventoryItem,
    StockBatch,
    StockLocation,
)


class CatalogueService:
    """Items, categories, locations and batches."""

    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # --- categories --------------------------------------------------------

    async def list_categories(self, *, organization_id: uuid.UUID) -> list[InventoryCategory]:
        stmt = (
            select(InventoryCategory)
            .where(InventoryCategory.organization_id == organization_id)
            .order_by(InventoryCategory.name)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_category(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> InventoryCategory:
        code = code.strip().upper()
        name = name.strip()
        if not code or not name:
            raise ValidationFailed("A category needs a code and a name")
        await self._refuse_duplicate_code(
            InventoryCategory, organization_id=organization_id, code=code, noun="category"
        )
        category = InventoryCategory(organization_id=organization_id, code=code, name=name)
        self._session.add(category)
        await self._session.flush()
        self._audit.record(
            events.ITEM_CREATED,
            organization_id=organization_id,
            target_type="inventory_category",
            target_id=category.id,
            target_label=category.name,
            actor_user_id=actor_user_id,
            new_values={"code": code, "name": name},
        )
        return category

    # --- locations ---------------------------------------------------------

    async def list_locations(self, *, organization_id: uuid.UUID) -> list[StockLocation]:
        stmt = (
            select(StockLocation)
            .where(StockLocation.organization_id == organization_id)
            .order_by(StockLocation.is_default.desc(), StockLocation.name)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_location(
        self, *, organization_id: uuid.UUID, location_id: uuid.UUID
    ) -> StockLocation:
        stmt = select(StockLocation).where(
            StockLocation.organization_id == organization_id,
            StockLocation.id == location_id,
        )
        location = (await self._session.execute(stmt)).scalar_one_or_none()
        if location is None:
            raise NotFound("Stock location not found")
        return location

    async def default_location(self, *, organization_id: uuid.UUID) -> StockLocation:
        """Where a delivery lands when nobody says otherwise.

        A clinic that has entered exactly one cupboard should not have to
        choose it every time, and one that has entered none is told to make
        one rather than having a location invented for it.
        """
        stmt = select(StockLocation).where(
            StockLocation.organization_id == organization_id,
            StockLocation.is_default.is_(True),
            StockLocation.is_active.is_(True),
        )
        location = (await self._session.execute(stmt)).scalar_one_or_none()
        if location is not None:
            return location
        locations = list(await self.list_locations(organization_id=organization_id))
        active = [loc for loc in locations if loc.is_active]
        if len(active) == 1:
            return active[0]
        raise ValidationFailed(
            "No default stock location has been set, so where this belongs "
            "has to be stated. Mark one location as the default, or name the "
            "location on the request.",
            details={"setting": "default_stock_location"},
        )

    async def create_location(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        is_default: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> StockLocation:
        code = code.strip().upper()
        name = name.strip()
        if not code or not name:
            raise ValidationFailed("A location needs a code and a name")
        await self._refuse_duplicate_code(
            StockLocation, organization_id=organization_id, code=code, noun="location"
        )
        if is_default:
            await self._clear_default(organization_id=organization_id)
        location = StockLocation(
            organization_id=organization_id, code=code, name=name, is_default=is_default
        )
        self._session.add(location)
        await self._session.flush()
        self._audit.record(
            events.ITEM_CREATED,
            organization_id=organization_id,
            target_type="stock_location",
            target_id=location.id,
            target_label=location.name,
            actor_user_id=actor_user_id,
            new_values={"code": code, "name": name, "is_default": is_default},
        )
        return location

    async def _clear_default(self, *, organization_id: uuid.UUID) -> None:
        """Only one place can be the default, and the index says so too."""
        stmt = select(StockLocation).where(
            StockLocation.organization_id == organization_id,
            StockLocation.is_default.is_(True),
        )
        for existing in (await self._session.execute(stmt)).scalars().all():
            existing.is_default = False
        await self._session.flush()

    # --- items -------------------------------------------------------------

    async def list_items(
        self,
        *,
        organization_id: uuid.UUID,
        search: str | None = None,
        category_id: uuid.UUID | None = None,
        active_only: bool = True,
    ) -> list[InventoryItem]:
        stmt = select(InventoryItem).where(InventoryItem.organization_id == organization_id)
        if active_only:
            stmt = stmt.where(InventoryItem.is_active.is_(True))
        if category_id is not None:
            stmt = stmt.where(InventoryItem.category_id == category_id)
        if search:
            pattern = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                func.lower(InventoryItem.name).like(pattern)
                | func.lower(InventoryItem.code).like(pattern)
            )
        stmt = stmt.order_by(InventoryItem.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_item(self, *, organization_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
        stmt = select(InventoryItem).where(
            InventoryItem.organization_id == organization_id, InventoryItem.id == item_id
        )
        item = (await self._session.execute(stmt)).scalar_one_or_none()
        if item is None:
            raise NotFound("Material not found")
        return item

    async def create_item(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        unit: str,
        category_id: uuid.UUID | None = None,
        reorder_level: object | None = None,
        is_batch_tracked: bool = False,
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> InventoryItem:
        code = code.strip().upper()
        name = name.strip()
        unit = unit.strip()
        if not code or not name or not unit:
            raise ValidationFailed("A material needs a code, a name and a unit")
        await self._refuse_duplicate_code(
            InventoryItem, organization_id=organization_id, code=code, noun="material"
        )
        if category_id is not None:
            await self._assert_category(organization_id=organization_id, category_id=category_id)
        item = InventoryItem(
            organization_id=organization_id,
            code=code,
            name=name,
            unit=unit,
            category_id=category_id,
            reorder_level=reorder_level,
            is_batch_tracked=is_batch_tracked,
            notes=(notes or "").strip() or None,
        )
        self._session.add(item)
        await self._session.flush()
        self._audit.record(
            events.ITEM_CREATED,
            organization_id=organization_id,
            target_type="inventory_item",
            target_id=item.id,
            target_label=item.name,
            actor_user_id=actor_user_id,
            new_values={
                "code": code,
                "name": name,
                "unit": unit,
                "batch_tracked": is_batch_tracked,
            },
        )
        return item

    async def update_item(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        **changes: object,
    ) -> InventoryItem:
        """Edit a material. Its code never changes once stock has moved.

        Batch tracking cannot be switched on for an item that already has
        movements without a batch, because the stock already on the shelf
        would belong to no lot and the expiry report would quietly omit it.
        """
        item = await self.get_item(organization_id=organization_id, item_id=item_id)
        before = {
            "name": item.name,
            "unit": item.unit,
            "reorder_level": str(item.reorder_level) if item.reorder_level is not None else None,
            "is_batch_tracked": item.is_batch_tracked,
            "is_active": item.is_active,
        }
        if "category_id" in changes and changes["category_id"] is not None:
            await self._assert_category(
                organization_id=organization_id,
                category_id=changes["category_id"],  # type: ignore[arg-type]
            )
        if (
            changes.get("is_batch_tracked")
            and not item.is_batch_tracked
            and await self._has_untracked_movements(
                organization_id=organization_id, item_id=item_id
            )
        ):
            raise Conflict(
                "This material already has stock recorded without a batch, so "
                "batch tracking cannot be switched on: the stock on the shelf "
                "would belong to no lot and would be left out of the expiry "
                "report. Count it out and in again under a batch.",
                details={"item_id": str(item_id)},
            )
        for field in (
            "name",
            "unit",
            "category_id",
            "reorder_level",
            "is_batch_tracked",
            "notes",
            "is_active",
        ):
            if field in changes:
                value = changes[field]
                if isinstance(value, str):
                    value = value.strip() or None if field == "notes" else value.strip()
                setattr(item, field, value)
        if not item.name or not item.unit:
            raise ValidationFailed("A material needs a name and a unit")
        await self._session.flush()
        self._audit.record(
            events.ITEM_UPDATED,
            organization_id=organization_id,
            target_type="inventory_item",
            target_id=item.id,
            target_label=item.name,
            actor_user_id=actor_user_id,
            old_values=before,
            new_values={
                "name": item.name,
                "unit": item.unit,
                "reorder_level": str(item.reorder_level)
                if item.reorder_level is not None
                else None,
                "is_batch_tracked": item.is_batch_tracked,
                "is_active": item.is_active,
            },
        )
        return item

    async def _has_untracked_movements(
        self, *, organization_id: uuid.UUID, item_id: uuid.UUID
    ) -> bool:
        from app.modules.inventory.models import StockMovement

        stmt = select(func.count()).where(
            StockMovement.organization_id == organization_id,
            StockMovement.item_id == item_id,
            StockMovement.batch_id.is_(None),
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    # --- batches -----------------------------------------------------------

    async def list_batches(
        self, *, organization_id: uuid.UUID, item_id: uuid.UUID | None = None
    ) -> list[StockBatch]:
        stmt = select(StockBatch).where(StockBatch.organization_id == organization_id)
        if item_id is not None:
            stmt = stmt.where(StockBatch.item_id == item_id)
        return list(
            (await self._session.execute(stmt.order_by(StockBatch.expires_on.nulls_last())))
            .scalars()
            .all()
        )

    async def get_batch(self, *, organization_id: uuid.UUID, batch_id: uuid.UUID) -> StockBatch:
        stmt = select(StockBatch).where(
            StockBatch.organization_id == organization_id, StockBatch.id == batch_id
        )
        batch = (await self._session.execute(stmt)).scalar_one_or_none()
        if batch is None:
            raise NotFound("Batch not found")
        return batch

    async def ensure_batch(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        code: str,
        expires_on: date | None = None,
    ) -> StockBatch:
        """Find this lot or create it, so receiving never fails on a typo.

        The expiry date is only ever *set*, never silently changed: a second
        delivery of the same lot number with a different date is a question
        for a person, not something to overwrite.
        """
        code = code.strip()
        if not code:
            raise ValidationFailed("A batch needs its lot number")
        stmt = select(StockBatch).where(
            StockBatch.organization_id == organization_id,
            StockBatch.item_id == item_id,
            StockBatch.code == code,
        )
        batch = (await self._session.execute(stmt)).scalar_one_or_none()
        if batch is None:
            batch = StockBatch(
                organization_id=organization_id,
                item_id=item_id,
                code=code,
                expires_on=expires_on,
            )
            self._session.add(batch)
            await self._session.flush()
            return batch
        if expires_on is not None and batch.expires_on is None:
            batch.expires_on = expires_on
            await self._session.flush()
        elif (
            expires_on is not None
            and batch.expires_on is not None
            and batch.expires_on != expires_on
        ):
            raise Conflict(
                f"Batch {code} is already recorded as expiring on "
                f"{batch.expires_on.isoformat()}. Two different dates for one "
                "lot number is a question for a person, not something to "
                "overwrite.",
                details={"batch_id": str(batch.id), "expires_on": batch.expires_on.isoformat()},
            )
        return batch

    # --- shared ------------------------------------------------------------

    async def _assert_category(self, *, organization_id: uuid.UUID, category_id: uuid.UUID) -> None:
        stmt = select(InventoryCategory.id).where(
            InventoryCategory.organization_id == organization_id,
            InventoryCategory.id == category_id,
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is None:
            raise NotFound("Category not found")

    async def _refuse_duplicate_code(
        self, model: type, *, organization_id: uuid.UUID, code: str, noun: str
    ) -> None:
        stmt = select(model.id).where(  # type: ignore[attr-defined]
            model.organization_id == organization_id,  # type: ignore[attr-defined]
            model.code == code,  # type: ignore[attr-defined]
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is not None:
            raise Conflict(
                f"A {noun} with code {code} already exists",
                details={"code": code},
            )
