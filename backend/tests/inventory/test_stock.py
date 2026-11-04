"""The stock ledger: balances that are sums, and a shelf that cannot go short.

The tests that matter most here are the ones about what *does not* happen. A
consumption posts nothing, because what the material cost is already in the
books from the supplier invoice that bought it. A count moves nothing until
somebody who did not count it approves the difference. And no movement, by
any route, leaves a shelf holding less than nothing.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.models import JournalEntry
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.inventory.enums import AdjustmentStatus, StockMovementKind, StockMovementSource
from app.modules.inventory.models import StockMovement

COUNT_DAY = date(2026, 10, 15)


async def a_shelf(catalogue, org, *, code="MAIN", is_default=True):
    return await catalogue.create_location(
        organization_id=org.id, code=code, name="Store cupboard", is_default=is_default
    )


async def a_material(catalogue, org, *, code="COMP-A2", unit="box", **kwargs):
    return await catalogue.create_item(
        organization_id=org.id, code=code, name="Composite A2", unit=unit, **kwargs
    )


async def stock_of(stock, org, item, location, quantity="10"):
    """Put stock on a shelf the only way the system allows: a delivery."""
    return await stock.receive(
        organization_id=org.id,
        item_id=item.id,
        location_id=location.id,
        quantity=Decimal(quantity),
        receipt_line_id=uuid.uuid4(),
    )


class TestABalanceIsASum:
    async def test_a_new_material_has_no_stock(self, catalogue, stock, org_a) -> None:
        item = await a_material(catalogue, org_a)
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("0")

    async def test_receiving_then_consuming_leaves_the_difference(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        await stock.consume(
            organization_id=org_a.id, item_id=item.id, quantity=Decimal("3"), location_id=shelf.id
        )
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("7")

    async def test_the_balance_is_recomputed_not_stored(self, catalogue, stock, db, org_a) -> None:
        """Deleting a movement changes the balance, which a column would not."""
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        movement = await stock_of(stock, org_a, item, shelf, "10")
        await db.delete(movement)
        await db.flush()
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("0")

    async def test_balances_are_split_by_shelf(self, catalogue, stock, org_a) -> None:
        cupboard = await a_shelf(catalogue, org_a)
        surgery = await a_shelf(catalogue, org_a, code="SURGERY", is_default=False)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, cupboard, "10")
        await stock_of(stock, org_a, item, surgery, "4")

        rows = await stock.balances(organization_id=org_a.id)
        by_location = {row.location_id: row.quantity for row in rows}
        assert by_location[cupboard.id] == Decimal("10")
        assert by_location[surgery.id] == Decimal("4")
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("14")


class TestAShelfCannotGoShort:
    async def test_consuming_more_than_is_there_is_refused(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "2")
        with pytest.raises(Conflict) as refusal:
            await stock.consume(
                organization_id=org_a.id,
                item_id=item.id,
                quantity=Decimal("3"),
                location_id=shelf.id,
            )
        # The refusal says what is actually on the shelf, because the next
        # thing the person does is go and look at it.
        assert Decimal(refusal.value.details["available"]) == Decimal("2")
        assert Decimal(refusal.value.details["requested"]) == Decimal("3")

    async def test_consuming_exactly_what_is_there_is_allowed(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "2")
        await stock.consume(
            organization_id=org_a.id, item_id=item.id, quantity=Decimal("2"), location_id=shelf.id
        )
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("0")

    async def test_stock_on_another_shelf_does_not_count(
        self, catalogue, stock, org_a
    ) -> None:
        """Ten boxes in the cupboard do not help the surgery that has none."""
        cupboard = await a_shelf(catalogue, org_a)
        surgery = await a_shelf(catalogue, org_a, code="SURGERY", is_default=False)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, cupboard, "10")
        with pytest.raises(Conflict):
            await stock.consume(
                organization_id=org_a.id,
                item_id=item.id,
                quantity=Decimal("1"),
                location_id=surgery.id,
            )

    async def test_a_write_off_cannot_go_short_either(self, catalogue, stock, org_a) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "1")
        with pytest.raises(Conflict):
            await stock.write_off(
                organization_id=org_a.id,
                item_id=item.id,
                quantity=Decimal("5"),
                reason="Dropped",
                location_id=shelf.id,
            )

    async def test_a_transfer_cannot_move_what_is_not_there(
        self, catalogue, stock, org_a
    ) -> None:
        cupboard = await a_shelf(catalogue, org_a)
        surgery = await a_shelf(catalogue, org_a, code="SURGERY", is_default=False)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, cupboard, "1")
        with pytest.raises(Conflict):
            await stock.transfer(
                organization_id=org_a.id,
                item_id=item.id,
                from_location_id=cupboard.id,
                to_location_id=surgery.id,
                quantity=Decimal("2"),
            )

    async def test_the_database_refuses_a_movement_of_nothing(
        self, catalogue, stock, db, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        db.add(
            StockMovement(
                organization_id=org_a.id,
                item_id=item.id,
                location_id=shelf.id,
                kind=StockMovementKind.RECEIPT,
                quantity=Decimal("0"),
                occurred_on=COUNT_DAY,
                source_kind=StockMovementSource.MANUAL,
                recorded_at=func.now(),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    async def test_the_database_refuses_a_receipt_that_removes_stock(
        self, catalogue, stock, db, org_a
    ) -> None:
        """The sign and the reason must agree, whatever wrote the row."""
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        db.add(
            StockMovement(
                organization_id=org_a.id,
                item_id=item.id,
                location_id=shelf.id,
                kind=StockMovementKind.RECEIPT,
                quantity=Decimal("-5"),
                occurred_on=COUNT_DAY,
                source_kind=StockMovementSource.MANUAL,
                recorded_at=func.now(),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


class TestNothingHerePosts:
    async def test_consuming_a_material_writes_no_journal_entry(
        self, catalogue, stock, db, org_a
    ) -> None:
        """The cost is already in the books, from the bill that bought it.

        Moving that cost to an expense as the material is used needs the
        valuation method nobody has declared, so this records the quantity
        and leaves the ledger alone.
        """
        before = (
            await db.execute(
                select(func.count()).select_from(JournalEntry).where(
                    JournalEntry.organization_id == org_a.id
                )
            )
        ).scalar_one()
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        await stock.consume(
            organization_id=org_a.id, item_id=item.id, quantity=Decimal("4"), location_id=shelf.id
        )
        await stock.write_off(
            organization_id=org_a.id,
            item_id=item.id,
            quantity=Decimal("1"),
            reason="Expired",
            location_id=shelf.id,
        )
        after = (
            await db.execute(
                select(func.count()).select_from(JournalEntry).where(
                    JournalEntry.organization_id == org_a.id
                )
            )
        ).scalar_one()
        assert after == before

    async def test_a_transfer_does_not_change_what_the_clinic_owns(
        self, catalogue, stock, org_a
    ) -> None:
        cupboard = await a_shelf(catalogue, org_a)
        surgery = await a_shelf(catalogue, org_a, code="SURGERY", is_default=False)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, cupboard, "10")
        await stock.transfer(
            organization_id=org_a.id,
            item_id=item.id,
            from_location_id=cupboard.id,
            to_location_id=surgery.id,
            quantity=Decimal("4"),
        )
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("10")
        assert await stock.on_hand(
            organization_id=org_a.id, item_id=item.id, location_id=surgery.id
        ) == Decimal("4")

    async def test_a_transfer_to_the_same_shelf_is_refused(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        with pytest.raises(ValidationFailed):
            await stock.transfer(
                organization_id=org_a.id,
                item_id=item.id,
                from_location_id=shelf.id,
                to_location_id=shelf.id,
                quantity=Decimal("1"),
            )


class TestWhatIsAudited:
    async def test_a_write_off_is_audited(self, catalogue, stock, db, org_a) -> None:
        """Destroying value on one person's say-so is audited, like a till
        discrepancy, and for the same reason."""
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        await stock.write_off(
            organization_id=org_a.id,
            item_id=item.id,
            quantity=Decimal("2"),
            reason="Out of date",
            location_id=shelf.id,
        )
        await db.flush()
        rows = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.STOCK_WRITTEN_OFF,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].new_values["reason"] == "Out of date"

    async def test_a_write_off_without_a_reason_is_refused(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        with pytest.raises(ValidationFailed):
            await stock.write_off(
                organization_id=org_a.id,
                item_id=item.id,
                quantity=Decimal("1"),
                reason="   ",
                location_id=shelf.id,
            )

    async def test_consumption_against_a_treatment_names_it(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        treatment_id = uuid.uuid4()
        movement = await stock.consume(
            organization_id=org_a.id,
            item_id=item.id,
            quantity=Decimal("1"),
            location_id=shelf.id,
            treatment_item_id=treatment_id,
        )
        assert movement.source_kind is StockMovementSource.TREATMENT_ITEM
        assert movement.source_id == treatment_id


class TestBatchesAndExpiry:
    async def test_a_batch_tracked_item_refuses_a_movement_with_no_lot(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a, is_batch_tracked=True)
        with pytest.raises(ValidationFailed):
            await stock.receive(
                organization_id=org_a.id,
                item_id=item.id,
                location_id=shelf.id,
                quantity=Decimal("5"),
                receipt_line_id=uuid.uuid4(),
            )

    async def test_a_batch_belonging_to_another_material_is_refused(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        anaesthetic = await a_material(catalogue, org_a, code="LIDO", is_batch_tracked=True)
        composite = await a_material(catalogue, org_a, code="COMP", is_batch_tracked=True)
        batch = await catalogue.ensure_batch(
            organization_id=org_a.id, item_id=anaesthetic.id, code="L-1"
        )
        with pytest.raises(ValidationFailed):
            await stock.receive(
                organization_id=org_a.id,
                item_id=composite.id,
                location_id=shelf.id,
                quantity=Decimal("1"),
                receipt_line_id=uuid.uuid4(),
                batch_id=batch.id,
            )

    async def test_expiring_reports_only_lots_with_stock_left(
        self, catalogue, stock, org_a
    ) -> None:
        """A used-up lot is not a problem however out of date it is."""
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a, is_batch_tracked=True)
        soon = await catalogue.ensure_batch(
            organization_id=org_a.id,
            item_id=item.id,
            code="SOON",
            expires_on=COUNT_DAY + timedelta(days=10),
        )
        gone = await catalogue.ensure_batch(
            organization_id=org_a.id,
            item_id=item.id,
            code="GONE",
            expires_on=COUNT_DAY + timedelta(days=5),
        )
        await stock.receive(
            organization_id=org_a.id,
            item_id=item.id,
            location_id=shelf.id,
            quantity=Decimal("3"),
            receipt_line_id=uuid.uuid4(),
            batch_id=soon.id,
        )
        await stock.receive(
            organization_id=org_a.id,
            item_id=item.id,
            location_id=shelf.id,
            quantity=Decimal("2"),
            receipt_line_id=uuid.uuid4(),
            batch_id=gone.id,
        )
        await stock.consume(
            organization_id=org_a.id,
            item_id=item.id,
            quantity=Decimal("2"),
            location_id=shelf.id,
            batch_id=gone.id,
        )
        rows = await stock.expiring(organization_id=org_a.id, within_days=30, on=COUNT_DAY)
        assert [row.batch_code for row in rows] == ["SOON"]
        assert rows[0].days_remaining == 10

    async def test_a_lot_already_out_of_date_is_reported(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a, is_batch_tracked=True)
        batch = await catalogue.ensure_batch(
            organization_id=org_a.id,
            item_id=item.id,
            code="OLD",
            expires_on=COUNT_DAY - timedelta(days=3),
        )
        await stock.receive(
            organization_id=org_a.id,
            item_id=item.id,
            location_id=shelf.id,
            quantity=Decimal("1"),
            receipt_line_id=uuid.uuid4(),
            batch_id=batch.id,
        )
        rows = await stock.expiring(organization_id=org_a.id, within_days=0, on=COUNT_DAY)
        assert rows[0].days_remaining == -3

    async def test_one_lot_number_cannot_carry_two_expiry_dates(
        self, catalogue, org_a
    ) -> None:
        item = await a_material(catalogue, org_a, is_batch_tracked=True)
        await catalogue.ensure_batch(
            organization_id=org_a.id, item_id=item.id, code="L-1", expires_on=COUNT_DAY
        )
        with pytest.raises(Conflict):
            await catalogue.ensure_batch(
                organization_id=org_a.id,
                item_id=item.id,
                code="L-1",
                expires_on=COUNT_DAY + timedelta(days=30),
            )


class TestLowStock:
    async def test_only_items_with_a_level_are_reported(
        self, catalogue, stock, org_a
    ) -> None:
        """An item nobody set a level on is never flagged: a made-up
        threshold trains people to ignore the list."""
        shelf = await a_shelf(catalogue, org_a)
        watched = await a_material(catalogue, org_a, code="WATCHED", reorder_level=Decimal("5"))
        ignored = await a_material(catalogue, org_a, code="IGNORED")
        await stock_of(stock, org_a, watched, shelf, "4")
        await stock_of(stock, org_a, ignored, shelf, "0.5")

        rows = await stock.low_stock(organization_id=org_a.id)
        assert [row.code for row in rows] == ["WATCHED"]
        assert rows[0].on_hand == Decimal("4")

    async def test_an_item_never_received_is_still_low(self, catalogue, org_a, stock) -> None:
        await a_material(catalogue, org_a, code="NEVER", reorder_level=Decimal("2"))
        rows = await stock.low_stock(organization_id=org_a.id)
        assert [row.code for row in rows] == ["NEVER"]
        assert rows[0].on_hand == Decimal("0")

    async def test_stock_above_the_level_is_not_reported(
        self, catalogue, stock, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a, reorder_level=Decimal("5"))
        await stock_of(stock, org_a, item, shelf, "6")
        assert await stock.low_stock(organization_id=org_a.id) == []


class TestTheCount:
    async def test_a_count_moves_nothing_until_it_is_approved(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
            counted_on=COUNT_DAY,
        )
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("10")

    async def test_approving_writes_the_difference(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        counter = org_a.owner.id
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
            counted_on=COUNT_DAY,
            actor_user_id=counter,
        )
        await stock_counts.propose(
            organization_id=org_a.id, adjustment_id=count.id, actor_user_id=counter
        )
        await stock_counts.approve(
            organization_id=org_a.id,
            adjustment_id=count.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("8")

    async def test_the_person_who_counted_cannot_approve_their_own_count(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        """The control holds even when one person holds both permissions,
        which in a clinic of three they will."""
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        counter = org_a.owner.id
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
            actor_user_id=counter,
        )
        await stock_counts.propose(
            organization_id=org_a.id, adjustment_id=count.id, actor_user_id=counter
        )
        with pytest.raises(Conflict) as refusal:
            await stock_counts.approve(
                organization_id=org_a.id, adjustment_id=count.id, actor_user_id=counter
            )
        assert refusal.value.details["control"] == "inventory.adjustment.approve"

    async def test_a_draft_cannot_be_approved(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
        )
        with pytest.raises(Conflict):
            await stock_counts.approve(
                organization_id=org_a.id,
                adjustment_id=count.id,
                actor_user_id=org_a.members["accountant"][0].id,
            )

    async def test_approving_twice_is_refused(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
        )
        await stock_counts.propose(organization_id=org_a.id, adjustment_id=count.id)
        await stock_counts.approve(
            organization_id=org_a.id,
            adjustment_id=count.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        with pytest.raises(Conflict):
            await stock_counts.approve(
                organization_id=org_a.id,
                adjustment_id=count.id,
                actor_user_id=org_a.members["accountant"][0].id,
            )

    async def test_a_count_that_agrees_moves_nothing(
        self, catalogue, stock, stock_counts, db, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("10"), None)],
        )
        await stock_counts.propose(organization_id=org_a.id, adjustment_id=count.id)
        await stock_counts.approve(
            organization_id=org_a.id,
            adjustment_id=count.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        movements = (
            await db.execute(
                select(func.count()).select_from(StockMovement).where(
                    StockMovement.organization_id == org_a.id,
                    StockMovement.kind == StockMovementKind.ADJUSTMENT,
                )
            )
        ).scalar_one()
        assert movements == 0

    async def test_the_difference_is_recomputed_at_approval(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        """Stock may legitimately move between counting and approving.

        The approval writes the difference between what was counted and what
        the ledger says *now*, so the shelf ends up holding the counted
        figure rather than the counted figure plus whatever happened in
        between.
        """
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
        )
        await stock_counts.propose(organization_id=org_a.id, adjustment_id=count.id)
        # A treatment uses one while the count waits for approval.
        await stock.consume(
            organization_id=org_a.id, item_id=item.id, quantity=Decimal("1"), location_id=shelf.id
        )
        await stock_counts.approve(
            organization_id=org_a.id,
            adjustment_id=count.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("8")

    async def test_a_rejected_count_moves_nothing_and_says_why(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("2"), None)],
        )
        await stock_counts.propose(organization_id=org_a.id, adjustment_id=count.id)
        rejected = await stock_counts.reject(
            organization_id=org_a.id,
            adjustment_id=count.id,
            note="Count the second cupboard too",
            actor_user_id=org_a.members['accountant'][0].id,
        )
        assert rejected.status is AdjustmentStatus.REJECTED
        assert rejected.decision_note == "Count the second cupboard too"
        assert await stock.on_hand(organization_id=org_a.id, item_id=item.id) == Decimal("10")

    async def test_a_rejection_needs_a_note(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("2"), None)],
        )
        await stock_counts.propose(organization_id=org_a.id, adjustment_id=count.id)
        with pytest.raises(ValidationFailed):
            await stock_counts.reject(
                organization_id=org_a.id, adjustment_id=count.id, note=" "
            )

    async def test_a_count_keeps_what_the_system_believed(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), "Two boxes missing")],
        )
        lines = await stock_counts.lines(organization_id=org_a.id, adjustment_id=count.id)
        assert lines[0].system_quantity == Decimal("10")
        assert lines[0].counted_quantity == Decimal("8")
        assert lines[0].note == "Two boxes missing"

    async def test_an_approved_count_is_audited(
        self, catalogue, stock, stock_counts, db, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        count = await stock_counts.create(
            organization_id=org_a.id,
            location_id=shelf.id,
            reason="Monthly count",
            counted=[(item.id, None, Decimal("8"), None)],
        )
        await stock_counts.propose(organization_id=org_a.id, adjustment_id=count.id)
        await stock_counts.approve(
            organization_id=org_a.id,
            adjustment_id=count.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        await db.flush()
        rows = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.STOCK_ADJUSTED,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].new_values["lines_moved"] == 1

    async def test_counting_the_same_material_twice_on_one_sheet_is_refused(
        self, catalogue, stock, stock_counts, org_a
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        with pytest.raises(ValidationFailed):
            await stock_counts.create(
                organization_id=org_a.id,
                location_id=shelf.id,
                reason="Monthly count",
                counted=[
                    (item.id, None, Decimal("8"), None),
                    (item.id, None, Decimal("9"), None),
                ],
            )


class TestTenantIsolation:
    async def test_stock_in_one_clinic_is_invisible_to_another(
        self, catalogue, stock, org_a, org_b
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        assert await stock.balances(organization_id=org_b.id) == []
        assert await stock.on_hand(organization_id=org_b.id, item_id=item.id) == Decimal("0")

    async def test_another_clinics_material_cannot_be_consumed(
        self, catalogue, stock, org_a, org_b
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "10")
        with pytest.raises(NotFound):
            await stock.consume(
                organization_id=org_b.id,
                item_id=item.id,
                quantity=Decimal("1"),
                location_id=shelf.id,
            )

    async def test_a_count_cannot_reach_another_clinics_shelf(
        self, catalogue, stock_counts, org_a, org_b
    ) -> None:
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        with pytest.raises(NotFound):
            await stock_counts.create(
                organization_id=org_b.id,
                location_id=shelf.id,
                reason="Count",
                counted=[(item.id, None, Decimal("1"), None)],
            )


class TestTheCatalogue:
    async def test_a_duplicate_code_is_refused(self, catalogue, org_a) -> None:
        await a_material(catalogue, org_a, code="COMP")
        with pytest.raises(Conflict):
            await a_material(catalogue, org_a, code="COMP")

    async def test_two_clinics_may_use_the_same_code(self, catalogue, org_a, org_b) -> None:
        await a_material(catalogue, org_a, code="COMP")
        await a_material(catalogue, org_b, code="COMP")

    async def test_a_second_default_shelf_replaces_the_first(
        self, catalogue, org_a
    ) -> None:
        first = await a_shelf(catalogue, org_a, code="ONE")
        second = await a_shelf(catalogue, org_a, code="TWO")
        assert (await catalogue.default_location(organization_id=org_a.id)).id == second.id
        await catalogue.get_location(organization_id=org_a.id, location_id=first.id)

    async def test_the_only_shelf_is_the_default_without_being_marked(
        self, catalogue, org_a
    ) -> None:
        only = await a_shelf(catalogue, org_a, code="ONLY", is_default=False)
        assert (await catalogue.default_location(organization_id=org_a.id)).id == only.id

    async def test_with_no_shelf_the_system_asks_rather_than_invents_one(
        self, catalogue, org_a
    ) -> None:
        with pytest.raises(ValidationFailed) as refusal:
            await catalogue.default_location(organization_id=org_a.id)
        assert refusal.value.details["setting"] == "default_stock_location"

    async def test_batch_tracking_cannot_be_switched_on_over_existing_stock(
        self, catalogue, stock, org_a
    ) -> None:
        """The stock already there would belong to no lot, and the expiry
        report would quietly leave it out."""
        shelf = await a_shelf(catalogue, org_a)
        item = await a_material(catalogue, org_a)
        await stock_of(stock, org_a, item, shelf, "5")
        with pytest.raises(Conflict):
            await catalogue.update_item(
                organization_id=org_a.id, item_id=item.id, is_batch_tracked=True
            )

    async def test_batch_tracking_can_be_switched_on_before_any_stock(
        self, catalogue, org_a
    ) -> None:
        item = await a_material(catalogue, org_a)
        updated = await catalogue.update_item(
            organization_id=org_a.id, item_id=item.id, is_batch_tracked=True
        )
        assert updated.is_batch_tracked is True
