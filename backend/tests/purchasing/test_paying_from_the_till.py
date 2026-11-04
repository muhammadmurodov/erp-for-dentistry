"""Paying a supplier out of the drawer.

One act, two records that must not come apart: the posting that reduces the
payable, and the movement that reduces the cash. A payment that reached the
ledger and not the till would leave the evening count short by exactly the
amount nobody could explain — which is how a shortage becomes a suspicion
about a person.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.modules.cash.models import CashMovement
from app.modules.purchasing.enums import BillKind
from app.modules.purchasing.services.payables import BillLineInput

TODAY = date(2026, 10, 1)


async def a_supplier(suppliers, sales, *, name="Dental Depot"):
    return await suppliers.create(organization_id=sales.id, name=name)


async def a_registered_bill(payables, supplier, sales, *, total="300000"):
    bill = await payables.create_bill(
        organization_id=sales.id,
        supplier_id=supplier.id,
        kind=BillKind.MATERIALS,
        invoice_date=TODAY,
        lines=[
            BillLineInput(
                description="Composite",
                quantity=Decimal("1"),
                unit_price=Decimal(total),
            )
        ],
    )
    return await payables.register_bill(organization_id=sales.id, invoice_id=bill.id)


@pytest.fixture
async def till_with_cash(cash, till, sales, billing):
    """An open session holding 1 000 000 so'm, taken in from a patient.

    Not opened with a float: the first session on a register refuses an
    opening float the ledger does not back, which is a control and not an
    obstacle. So the money gets into the drawer the way it really does —
    somebody pays cash.
    """
    from app.modules.sales.enums import PaymentMethod

    session = await cash.open_session(
        organization_id=sales.id,
        register_id=till.id,
        business_date=TODAY,
        opening_float=Decimal("0"),
    )
    payment = await billing.record_payment(
        organization_id=sales.id,
        customer_id=sales.customer.id,
        amount=Decimal("1000000"),
        method=PaymentMethod.CASH,
        received_on=TODAY,
    )
    await cash.record_payment_receipt(
        organization_id=sales.id,
        session_id=session.id,
        payment_id=payment.id,
        amount=Decimal("1000000"),
    )
    return session


class TestPayingFromTheTill:
    async def test_it_posts_and_moves_the_drawer_together(
        self, supplier_till, payables, suppliers, sales, till_with_cash, cash, db: AsyncSession
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        bill = await a_registered_bill(payables, supplier, sales)

        paid = await supplier_till.pay_from_till(
            organization_id=sales.id,
            session_id=till_with_cash.id,
            supplier_id=supplier.id,
            amount=Decimal("300000"),
            invoice_id=bill.id,
        )
        await db.flush()

        balance = await payables.bill_balance(organization_id=sales.id, invoice_id=bill.id)
        assert balance.remaining == Decimal("0.00")

        movement = await supplier_till.movement_for(
            organization_id=sales.id, supplier_payment_id=paid.payment_id
        )
        assert movement is not None
        assert movement.amount == Decimal("300000.0000")

        totals = await cash.totals(organization_id=sales.id, session_id=till_with_cash.id)
        assert totals.expected == Decimal("700000.00")

    async def test_the_till_cannot_pay_more_than_it_holds(
        self, supplier_till, payables, suppliers, sales, till_with_cash
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        with pytest.raises(ValidationFailed) as excinfo:
            await supplier_till.pay_from_till(
                organization_id=sales.id,
                session_id=till_with_cash.id,
                supplier_id=supplier.id,
                amount=Decimal("1500000"),
            )
        assert "expected" in excinfo.value.details

    async def test_nothing_is_recorded_when_the_till_refuses(
        self, supplier_till, payables, suppliers, sales, till_with_cash, db: AsyncSession
    ) -> None:
        """The drawer is checked before the payment is created, so a refusal
        cannot leave a posted payment with no movement behind it."""
        supplier = await a_supplier(suppliers, sales)
        with pytest.raises(ValidationFailed):
            await supplier_till.pay_from_till(
                organization_id=sales.id,
                session_id=till_with_cash.id,
                supplier_id=supplier.id,
                amount=Decimal("1500000"),
            )
        assert await payables.list_payments(organization_id=sales.id, supplier_id=supplier.id) == []

    async def test_a_closed_till_cannot_pay(
        self, supplier_till, suppliers, sales, cash, till
    ) -> None:
        session = await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=TODAY,
            opening_float=Decimal("0"),
        )
        await cash.submit_count(
            organization_id=sales.id, session_id=session.id, counted_amount=Decimal("0")
        )
        await cash.close_session(organization_id=sales.id, session_id=session.id)
        supplier = await a_supplier(suppliers, sales)
        with pytest.raises(Conflict):
            await supplier_till.pay_from_till(
                organization_id=sales.id,
                session_id=session.id,
                supplier_id=supplier.id,
                amount=Decimal("1000"),
            )

    async def test_an_advance_may_be_paid_from_the_till(
        self, supplier_till, payables, suppliers, sales, till_with_cash
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        paid = await supplier_till.pay_from_till(
            organization_id=sales.id,
            session_id=till_with_cash.id,
            supplier_id=supplier.id,
            amount=Decimal("250000"),
        )
        assert await payables.unallocated_of(
            organization_id=sales.id, payment_id=paid.payment_id
        ) == Decimal("250000.00")

    async def test_one_movement_per_payment(
        self, supplier_till, suppliers, sales, till_with_cash, db: AsyncSession
    ) -> None:
        """A partial unique index makes a second drawer movement for the same
        payment unrepresentable, not merely unlikely."""
        supplier = await a_supplier(suppliers, sales)
        paid = await supplier_till.pay_from_till(
            organization_id=sales.id,
            session_id=till_with_cash.id,
            supplier_id=supplier.id,
            amount=Decimal("100000"),
        )
        await db.flush()
        rows = (
            (
                await db.execute(
                    select(CashMovement).where(
                        CashMovement.organization_id == sales.id,
                        CashMovement.supplier_payment_id == paid.payment_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
