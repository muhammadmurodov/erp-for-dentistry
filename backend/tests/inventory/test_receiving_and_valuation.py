"""Stock arrives through the door the money came through, and is costed last.

Two things are checked here that neither module can check alone. First, that
recording a delivery is what puts stock on a shelf — there is no other way
in, so every quantity traces back to a delivery note. Second, that stock is
*counted* long before it is *costed*: while the valuation method is
undeclared the quantities are real and every value is null, and the moment
an accountant declares weighted average the costs come from the supplier
bills already on file rather than from anything new.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import ValidationFailed
from app.modules.accounting.models import JournalEntry
from app.modules.inventory.enums import InventoryValuationMethod, StockMovementKind
from app.modules.organization.models import Organization
from app.modules.purchasing.enums import BillKind
from app.modules.purchasing.services.payables import BillLineInput
from app.modules.purchasing.services.receiving import ReceiptLineInput

DELIVERY_DAY = date(2026, 10, 1)


async def declare(db, sales, method: InventoryValuationMethod) -> None:
    """What an accountant answering I1 actually does to the organization."""
    organization = await db.get(Organization, sales.id)
    organization.inventory_valuation_method = method.value
    await db.flush()


async def a_supplier(suppliers, sales, *, name="Dental Depot"):
    return await suppliers.create(organization_id=sales.id, name=name)


async def a_shelf(catalogue, sales):
    return await catalogue.create_location(
        organization_id=sales.id, code="MAIN", name="Store cupboard", is_default=True
    )


async def a_material(catalogue, sales, **kwargs):
    return await catalogue.create_item(
        organization_id=sales.id,
        code=kwargs.pop("code", "COMP-A2"),
        name=kwargs.pop("name", "Composite A2"),
        unit=kwargs.pop("unit", "box"),
        **kwargs,
    )


async def deliver(receiving, suppliers, catalogue, sales, *, item, quantity="10", **kwargs):
    supplier = kwargs.pop("supplier", None) or await a_supplier(suppliers, sales)
    receipt = await receiving.receive(
        organization_id=sales.id,
        supplier_id=supplier.id,
        received_on=DELIVERY_DAY,
        lines=[
            ReceiptLineInput(
                description="Composite, A2",
                quantity=Decimal(quantity),
                unit="box",
                item_id=item.id if item else None,
                **kwargs,
            )
        ],
    )
    return supplier, receipt


class TestADeliveryPutsStockOnTheShelf:
    async def test_receiving_a_named_material_shelves_it(
        self, receiving, suppliers, catalogue, stock, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        await deliver(receiving, suppliers, catalogue, sales, item=item, quantity="10")
        assert await stock.on_hand(organization_id=sales.id, item_id=item.id) == Decimal("10")

    async def test_a_line_naming_no_material_shelves_nothing(
        self, receiving, suppliers, catalogue, stock, sales
    ) -> None:
        """The clinic also buys things it does not keep stock of."""
        await a_shelf(catalogue, sales)
        await deliver(receiving, suppliers, catalogue, sales, item=None, quantity="1")
        assert await stock.balances(organization_id=sales.id) == []

    async def test_only_what_was_accepted_is_shelved(
        self, receiving, suppliers, catalogue, stock, sales
    ) -> None:
        """Goods turned away at the door never entered the clinic."""
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        await deliver(
            receiving,
            suppliers,
            catalogue,
            sales,
            item=item,
            quantity="10",
            rejected_quantity=Decimal("4"),
            rejected_reason="Two boxes crushed, two out of date",
        )
        assert await stock.on_hand(organization_id=sales.id, item_id=item.id) == Decimal("6")

    async def test_a_delivery_entirely_rejected_shelves_nothing(
        self, receiving, suppliers, catalogue, stock, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        await deliver(
            receiving,
            suppliers,
            catalogue,
            sales,
            item=item,
            quantity="5",
            rejected_quantity=Decimal("5"),
            rejected_reason="Wrong shade entirely",
        )
        assert await stock.on_hand(organization_id=sales.id, item_id=item.id) == Decimal("0")

    async def test_the_movement_names_the_delivery_line(
        self, receiving, suppliers, catalogue, stock, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        await deliver(receiving, suppliers, catalogue, sales, item=item)
        movements = await stock.movements(organization_id=sales.id, item_id=item.id)
        assert movements[0].kind is StockMovementKind.RECEIPT
        assert movements[0].source_id is not None
        assert movements[0].occurred_on == DELIVERY_DAY

    async def test_receiving_still_posts_nothing(
        self, receiving, suppliers, catalogue, stock, db, sales
    ) -> None:
        """Putting stock on a shelf did not turn a receipt into a posting."""
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        before = (
            await db.execute(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.organization_id == sales.id)
            )
        ).scalar_one()
        await deliver(receiving, suppliers, catalogue, sales, item=item)
        after = (
            await db.execute(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.organization_id == sales.id)
            )
        ).scalar_one()
        assert after == before

    async def test_a_batch_tracked_delivery_must_name_its_lot(
        self, receiving, suppliers, catalogue, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales, is_batch_tracked=True)
        with pytest.raises(ValidationFailed):
            await deliver(receiving, suppliers, catalogue, sales, item=item)

    async def test_a_batch_tracked_delivery_creates_the_lot_with_its_date(
        self, receiving, suppliers, catalogue, stock, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales, is_batch_tracked=True)
        expiry = DELIVERY_DAY + timedelta(days=200)
        await deliver(
            receiving,
            suppliers,
            catalogue,
            sales,
            item=item,
            quantity="3",
            batch_code="LOT-7",
            batch_expires_on=expiry,
        )
        batches = await catalogue.list_batches(organization_id=sales.id, item_id=item.id)
        assert [(b.code, b.expires_on) for b in batches] == [("LOT-7", expiry)]
        rows = await stock.expiring(organization_id=sales.id, within_days=365, on=DELIVERY_DAY)
        assert rows[0].quantity == Decimal("3")

    async def test_with_no_shelf_the_delivery_is_refused_rather_than_guessed(
        self, receiving, suppliers, catalogue, sales
    ) -> None:
        item = await a_material(catalogue, sales)
        with pytest.raises(ValidationFailed) as refusal:
            await deliver(receiving, suppliers, catalogue, sales, item=item)
        assert refusal.value.details["setting"] == "default_stock_location"


class TestCountedBeforeItIsCosted:
    async def test_undeclared_gives_quantities_and_no_value(
        self, receiving, suppliers, catalogue, valuation, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        await deliver(receiving, suppliers, catalogue, sales, item=item, quantity="10")

        result = await valuation.value(organization_id=sales.id)
        assert result.method is InventoryValuationMethod.UNDECLARED
        assert result.total_value is None
        assert result.unpriced_items == 1
        assert result.rows[0].quantity == Decimal("10")
        assert result.rows[0].unit_cost is None

    async def test_fifo_says_it_is_not_implemented_rather_than_guessing(
        self, catalogue, valuation, db, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        await declare(db, sales, InventoryValuationMethod.FIFO)
        with pytest.raises(ValidationFailed) as refusal:
            await valuation.value(organization_id=sales.id)
        assert refusal.value.details["setting"] == "inventory_valuation_method"

    async def test_weighted_average_comes_from_the_registered_bill(
        self, receiving, suppliers, catalogue, payables, valuation, db, sales
    ) -> None:
        """The cost is the one the clinic actually paid, and nothing new is
        stored to hold it."""
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        supplier, receipt = await deliver(
            receiving, suppliers, catalogue, sales, item=item, quantity="10"
        )
        lines = await receiving.lines(organization_id=sales.id, receipt_id=receipt.id)
        bill = await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=DELIVERY_DAY,
            supplier_reference="INV-1",
            lines=[
                BillLineInput(
                    description="Composite, A2",
                    quantity=Decimal("10"),
                    unit_price=Decimal("50000"),
                    receipt_line_id=lines[0].id,
                )
            ],
        )
        await payables.register_bill(organization_id=sales.id, invoice_id=bill.id)

        await declare(db, sales, InventoryValuationMethod.WEIGHTED_AVERAGE)

        result = await valuation.value(organization_id=sales.id)
        assert result.rows[0].unit_cost == Decimal("50000")
        assert result.rows[0].value == Decimal("500000")
        assert result.total_value == Decimal("500000")
        assert result.unpriced_items == 0

    async def test_an_unregistered_bill_prices_nothing(
        self, receiving, suppliers, catalogue, payables, valuation, db, sales
    ) -> None:
        """A draft is what somebody typed, not what the clinic owes."""
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        supplier, receipt = await deliver(
            receiving, suppliers, catalogue, sales, item=item, quantity="10"
        )
        lines = await receiving.lines(organization_id=sales.id, receipt_id=receipt.id)
        await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=DELIVERY_DAY,
            supplier_reference="INV-DRAFT",
            lines=[
                BillLineInput(
                    description="Composite, A2",
                    quantity=Decimal("10"),
                    unit_price=Decimal("50000"),
                    receipt_line_id=lines[0].id,
                )
            ],
        )
        await declare(db, sales, InventoryValuationMethod.WEIGHTED_AVERAGE)

        result = await valuation.value(organization_id=sales.id)
        assert result.rows[0].unit_cost is None
        assert result.unpriced_items == 1
        assert result.total_value == Decimal("0")

    async def test_two_deliveries_at_two_prices_average_by_quantity(
        self, receiving, suppliers, catalogue, payables, valuation, db, sales
    ) -> None:
        await a_shelf(catalogue, sales)
        item = await a_material(catalogue, sales)
        supplier = await a_supplier(suppliers, sales)

        for index, (quantity, unit_price) in enumerate(
            [(Decimal("10"), Decimal("50000")), (Decimal("30"), Decimal("70000"))]
        ):
            _supplier, receipt = await deliver(
                receiving,
                suppliers,
                catalogue,
                sales,
                item=item,
                quantity=str(quantity),
                supplier=supplier,
            )
            lines = await receiving.lines(organization_id=sales.id, receipt_id=receipt.id)
            bill = await payables.create_bill(
                organization_id=sales.id,
                supplier_id=supplier.id,
                kind=BillKind.MATERIALS,
                invoice_date=DELIVERY_DAY,
                supplier_reference=f"INV-{index}",
                lines=[
                    BillLineInput(
                        description="Composite, A2",
                        quantity=quantity,
                        unit_price=unit_price,
                        receipt_line_id=lines[0].id,
                    )
                ],
            )
            await payables.register_bill(organization_id=sales.id, invoice_id=bill.id)

        await declare(db, sales, InventoryValuationMethod.WEIGHTED_AVERAGE)

        result = await valuation.value(organization_id=sales.id)
        # (10 x 50 000 + 30 x 70 000) / 40 = 65 000, weighted by quantity
        # rather than a simple mean of the two prices, which would be 60 000.
        assert result.rows[0].unit_cost == Decimal("65000")
        assert result.rows[0].quantity == Decimal("40")
        assert result.total_value == Decimal("2600000")
