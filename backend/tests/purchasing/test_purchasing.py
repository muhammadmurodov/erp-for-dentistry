"""Ordering, receiving, invoicing and paying — four records, kept apart.

The tests that matter most here are the ones about what *does not* post. An
order and a receipt move no money, and a system that posts at either point
leaves a phantom payable behind when the order is cancelled or the delivery
is turned away.

The second group is about the same delivery not being paid for twice, which
is the commonest way a small business loses money to a supplier.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.modules.accounting.enums import SourceKind
from app.modules.accounting.models import JournalEntry, JournalLine, PostingEvent
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.purchasing.enums import (
    BillKind,
    BillStatus,
    OrderStatus,
    RequestStatus,
    SupplierPaymentMethod,
)
from app.modules.purchasing.services.payables import BillLineInput
from app.modules.purchasing.services.receiving import ReceiptLineInput
from app.modules.purchasing.services.suppliers import OrderLineInput, RequestLineInput

BILL_DATE = date(2026, 10, 1)


async def a_supplier(suppliers, sales, *, name="Dental Depot", tax_number=None):
    return await suppliers.create(organization_id=sales.id, name=name, tax_number=tax_number)


async def an_order(orders, suppliers, sales, *, amount="500000", quantity="10"):
    supplier = await a_supplier(suppliers, sales)
    order = await orders.create(
        organization_id=sales.id,
        supplier_id=supplier.id,
        ordered_on=BILL_DATE,
        lines=[
            OrderLineInput(
                description="Composite, A2",
                quantity=Decimal(quantity),
                unit_price=Decimal(amount),
                unit="box",
            )
        ],
    )
    return supplier, order


async def a_registered_bill(payables, suppliers, sales, *, total="500000", supplier=None):
    supplier = supplier or await a_supplier(suppliers, sales)
    bill = await payables.create_bill(
        organization_id=sales.id,
        supplier_id=supplier.id,
        kind=BillKind.MATERIALS,
        invoice_date=BILL_DATE,
        lines=[
            BillLineInput(
                description="Composite, A2",
                quantity=Decimal("1"),
                unit_price=Decimal(total),
            )
        ],
    )
    return supplier, await payables.register_bill(organization_id=sales.id, invoice_id=bill.id)


async def entries_for(db, organization_id, source_kind, source_id):
    stmt = select(PostingEvent).where(
        PostingEvent.organization_id == organization_id,
        PostingEvent.source_kind == source_kind,
        PostingEvent.source_id == str(source_id),
    )
    return list((await db.execute(stmt)).scalars().all())


class TestSuppliers:
    async def test_a_supplier_gets_a_code(self, suppliers, sales) -> None:
        supplier = await a_supplier(suppliers, sales)
        assert supplier.code.startswith("SUP-")
        assert supplier.is_active is True

    async def test_a_duplicate_tax_number_is_refused(self, suppliers, sales) -> None:
        """Two legal entities cannot share a STIR; a mistyped one can."""
        await a_supplier(suppliers, sales, tax_number="302233445")
        with pytest.raises(Conflict):
            await a_supplier(suppliers, sales, name="Another Depot", tax_number="302233445")

    async def test_the_search_does_not_match_a_telephone_number(self, suppliers, sales) -> None:
        supplier = await a_supplier(suppliers, sales)
        await suppliers.update(
            organization_id=sales.id,
            supplier_id=supplier.id,
            changes={"phone": "+998901234567"},
        )
        assert await suppliers.list_suppliers(organization_id=sales.id, query="9012345") == []

    async def test_a_supplier_is_invisible_from_another_organization(
        self, suppliers, sales, org_b
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        with pytest.raises(NotFound):
            await suppliers.get(organization_id=org_b.id, supplier_id=supplier.id)


class TestRequests:
    async def test_a_request_starts_as_a_draft(self, purchase_requests, sales) -> None:
        request = await purchase_requests.create(
            organization_id=sales.id,
            lines=[RequestLineInput(description="Composite", quantity=Decimal("5"))],
            reason="Nearly out",
        )
        assert request.status is RequestStatus.DRAFT
        assert request.code.startswith("PR-")

    async def test_an_empty_request_is_refused(self, purchase_requests, sales) -> None:
        with pytest.raises(ValidationFailed):
            await purchase_requests.create(organization_id=sales.id, lines=[])

    async def test_the_asker_may_not_approve_their_own_request(
        self, purchase_requests, sales, org_a
    ) -> None:
        request = await purchase_requests.create(
            organization_id=sales.id,
            lines=[RequestLineInput(description="Composite", quantity=Decimal("5"))],
            actor_user_id=org_a.owner.id,
        )
        await purchase_requests.set_status(
            organization_id=sales.id,
            request_id=request.id,
            status=RequestStatus.SUBMITTED,
            actor_user_id=org_a.owner.id,
        )
        with pytest.raises(PermissionDenied):
            await purchase_requests.set_status(
                organization_id=sales.id,
                request_id=request.id,
                status=RequestStatus.APPROVED,
                actor_user_id=org_a.owner.id,
            )

    async def test_a_clinic_too_small_to_separate_duties_may_self_approve(
        self, purchase_requests, sales, org_a
    ) -> None:
        """Recorded as self-approved, never hidden (ADR-041)."""
        request = await purchase_requests.create(
            organization_id=sales.id,
            lines=[RequestLineInput(description="Composite", quantity=Decimal("5"))],
            actor_user_id=org_a.owner.id,
        )
        await purchase_requests.set_status(
            organization_id=sales.id,
            request_id=request.id,
            status=RequestStatus.SUBMITTED,
            actor_user_id=org_a.owner.id,
        )
        decided = await purchase_requests.set_status(
            organization_id=sales.id,
            request_id=request.id,
            status=RequestStatus.APPROVED,
            actor_user_id=org_a.owner.id,
            duties_cannot_be_separated=True,
        )
        assert decided.status is RequestStatus.APPROVED
        assert decided.self_approved is True

    async def test_a_draft_cannot_jump_to_approved(self, purchase_requests, sales) -> None:
        request = await purchase_requests.create(
            organization_id=sales.id,
            lines=[RequestLineInput(description="Composite", quantity=Decimal("5"))],
        )
        with pytest.raises(Conflict):
            await purchase_requests.set_status(
                organization_id=sales.id,
                request_id=request.id,
                status=RequestStatus.APPROVED,
            )


class TestOrders:
    async def test_an_order_totals_its_lines(self, orders, suppliers, sales) -> None:
        _supplier, order = await an_order(orders, suppliers, sales)
        assert order.total == Decimal("5000000.00")
        assert order.status is OrderStatus.DRAFT

    async def test_an_order_posts_nothing(self, orders, suppliers, sales, db: AsyncSession) -> None:
        """A promise to buy is not a transaction."""
        _supplier, order = await an_order(orders, suppliers, sales)
        await db.flush()
        before = (await db.execute(select(JournalEntry))).scalars().all()
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
        )
        await db.flush()
        after = (await db.execute(select(JournalEntry))).scalars().all()
        assert len(before) == len(after)

    async def test_an_inactive_supplier_cannot_be_ordered_from(
        self, orders, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        await suppliers.update(
            organization_id=sales.id,
            supplier_id=supplier.id,
            changes={"is_active": False},
        )
        with pytest.raises(Conflict):
            await orders.create(
                organization_id=sales.id,
                supplier_id=supplier.id,
                lines=[
                    OrderLineInput(
                        description="Composite",
                        quantity=Decimal("1"),
                        unit_price=Decimal("1000"),
                    )
                ],
            )

    async def test_the_person_who_raised_an_order_may_not_approve_it(
        self, orders, suppliers, sales, org_a
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        order = await orders.create(
            organization_id=sales.id,
            supplier_id=supplier.id,
            lines=[
                OrderLineInput(
                    description="Composite",
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
            actor_user_id=org_a.owner.id,
        )
        with pytest.raises(PermissionDenied):
            await orders.set_status(
                organization_id=sales.id,
                order_id=order.id,
                status=OrderStatus.APPROVED,
                actor_user_id=org_a.owner.id,
            )

    async def test_closing_an_order_short_needs_a_reason(self, orders, suppliers, sales) -> None:
        _supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
        )
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.SENT
        )
        with pytest.raises(ValidationFailed):
            await orders.set_status(
                organization_id=sales.id, order_id=order.id, status=OrderStatus.CLOSED
            )

    async def test_a_cancelled_order_is_final(self, orders, suppliers, sales) -> None:
        _supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.CANCELLED
        )
        with pytest.raises(Conflict):
            await orders.set_status(
                organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
            )


class TestReceiving:
    async def test_receiving_posts_nothing(
        self, receiving, orders, suppliers, sales, db: AsyncSession
    ) -> None:
        """The workbook has no goods-received-not-invoiced account, and one
        is not invented here."""
        supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
        )
        await db.flush()
        before = len((await db.execute(select(JournalEntry))).scalars().all())
        await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            order_id=order.id,
            lines=[ReceiptLineInput(description="Composite, A2", quantity=Decimal("10"))],
        )
        await db.flush()
        after = len((await db.execute(select(JournalEntry))).scalars().all())
        assert before == after

    async def test_a_full_delivery_closes_the_order(
        self, receiving, orders, suppliers, sales
    ) -> None:
        supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
        )
        lines = await orders.lines(organization_id=sales.id, order_id=order.id)
        await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            order_id=order.id,
            lines=[
                ReceiptLineInput(
                    description="Composite, A2",
                    quantity=Decimal("10"),
                    order_line_id=lines[0].id,
                )
            ],
        )
        refreshed = await orders.get(organization_id=sales.id, order_id=order.id)
        assert refreshed.status is OrderStatus.RECEIVED

    async def test_a_part_delivery_leaves_the_order_open(
        self, receiving, orders, suppliers, sales
    ) -> None:
        supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
        )
        lines = await orders.lines(organization_id=sales.id, order_id=order.id)
        await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            order_id=order.id,
            lines=[
                ReceiptLineInput(
                    description="Composite, A2",
                    quantity=Decimal("4"),
                    order_line_id=lines[0].id,
                )
            ],
        )
        refreshed = await orders.get(organization_id=sales.id, order_id=order.id)
        assert refreshed.status is OrderStatus.PARTIALLY_RECEIVED

        outstanding = await receiving.outstanding(organization_id=sales.id, order_id=order.id)
        assert outstanding[0].outstanding == Decimal("6.0000")

    async def test_over_delivery_leaves_nothing_outstanding(
        self, receiving, orders, suppliers, sales
    ) -> None:
        """Never negative: "minus three outstanding" would read as a debt."""
        supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.APPROVED
        )
        lines = await orders.lines(organization_id=sales.id, order_id=order.id)
        await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            order_id=order.id,
            lines=[
                ReceiptLineInput(
                    description="Composite, A2",
                    quantity=Decimal("13"),
                    order_line_id=lines[0].id,
                )
            ],
        )
        outstanding = await receiving.outstanding(organization_id=sales.id, order_id=order.id)
        assert outstanding[0].outstanding == Decimal("0")

    async def test_goods_turned_away_must_say_why(self, receiving, suppliers, sales) -> None:
        supplier = await a_supplier(suppliers, sales)
        with pytest.raises(ValidationFailed):
            await receiving.receive(
                organization_id=sales.id,
                supplier_id=supplier.id,
                lines=[
                    ReceiptLineInput(
                        description="Composite",
                        quantity=Decimal("5"),
                        rejected_quantity=Decimal("2"),
                    )
                ],
            )

    async def test_a_delivery_with_no_order_is_allowed(self, receiving, suppliers, sales) -> None:
        """The clinic buys gypsum from a market stall; refusing to record it
        would move the purchase off the books, not stop it."""
        supplier = await a_supplier(suppliers, sales)
        receipt = await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            lines=[ReceiptLineInput(description="Gypsum", quantity=Decimal("2"))],
        )
        assert receipt.order_id is None

    async def test_a_cancelled_order_expects_no_delivery(
        self, receiving, orders, suppliers, sales
    ) -> None:
        supplier, order = await an_order(orders, suppliers, sales)
        await orders.set_status(
            organization_id=sales.id, order_id=order.id, status=OrderStatus.CANCELLED
        )
        with pytest.raises(Conflict):
            await receiving.receive(
                organization_id=sales.id,
                supplier_id=supplier.id,
                order_id=order.id,
                lines=[ReceiptLineInput(description="Composite", quantity=Decimal("1"))],
            )

    async def test_a_delivery_cannot_name_another_suppliers_order(
        self, receiving, orders, suppliers, sales
    ) -> None:
        _supplier, order = await an_order(orders, suppliers, sales)
        other = await a_supplier(suppliers, sales, name="Someone Else")
        with pytest.raises(ValidationFailed):
            await receiving.receive(
                organization_id=sales.id,
                supplier_id=other.id,
                order_id=order.id,
                lines=[ReceiptLineInput(description="Composite", quantity=Decimal("1"))],
            )


class TestBills:
    async def test_a_draft_bill_posts_nothing(
        self, payables, suppliers, sales, db: AsyncSession
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        bill = await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=BILL_DATE,
            lines=[
                BillLineInput(
                    description="Composite",
                    quantity=Decimal("1"),
                    unit_price=Decimal("500000"),
                )
            ],
        )
        await db.flush()
        assert bill.status is BillStatus.DRAFT
        assert await entries_for(db, sales.id, SourceKind.SUPPLIER_INVOICE, bill.id) == []

    async def test_registering_a_materials_bill_posts_stock_and_a_payable(
        self, payables, suppliers, sales, db: AsyncSession
    ) -> None:
        _supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await db.flush()
        posted = await entries_for(db, sales.id, SourceKind.SUPPLIER_INVOICE, bill.id)
        assert len(posted) == 1

        lines = (
            (
                await db.execute(
                    select(JournalLine).where(
                        JournalLine.journal_entry_id == posted[0].journal_entry_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(lines) == 2
        assert sum(line.debit for line in lines) == Decimal("500000.0000")
        assert sum(line.credit for line in lines) == Decimal("500000.0000")

    async def test_a_registered_bill_cannot_be_registered_twice(
        self, payables, suppliers, sales
    ) -> None:
        _supplier, bill = await a_registered_bill(payables, suppliers, sales)
        with pytest.raises(Conflict):
            await payables.register_bill(organization_id=sales.id, invoice_id=bill.id)

    async def test_the_same_supplier_reference_cannot_be_entered_twice(
        self, payables, suppliers, sales
    ) -> None:
        """The check that stops one delivery being paid for twice."""
        supplier = await a_supplier(suppliers, sales)
        line = BillLineInput(
            description="Composite", quantity=Decimal("1"), unit_price=Decimal("100000")
        )
        await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=BILL_DATE,
            supplier_reference="AKT-4471",
            lines=[line],
        )
        with pytest.raises(Conflict):
            await payables.create_bill(
                organization_id=sales.id,
                supplier_id=supplier.id,
                kind=BillKind.MATERIALS,
                invoice_date=BILL_DATE,
                supplier_reference="AKT-4471",
                lines=[line],
            )

    async def test_the_same_reference_is_free_for_another_supplier(
        self, payables, suppliers, sales
    ) -> None:
        line = BillLineInput(
            description="Composite", quantity=Decimal("1"), unit_price=Decimal("100000")
        )
        one = await a_supplier(suppliers, sales, name="Depot One")
        two = await a_supplier(suppliers, sales, name="Depot Two")
        for supplier in (one, two):
            await payables.create_bill(
                organization_id=sales.id,
                supplier_id=supplier.id,
                kind=BillKind.MATERIALS,
                invoice_date=BILL_DATE,
                supplier_reference="001",
                lines=[line],
            )

    async def test_a_delivery_line_may_be_billed_once(
        self, payables, receiving, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        receipt = await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            lines=[ReceiptLineInput(description="Composite", quantity=Decimal("5"))],
        )
        lines = await receiving.lines(organization_id=sales.id, receipt_id=receipt.id)
        bill_line = BillLineInput(
            description="Composite",
            quantity=Decimal("5"),
            unit_price=Decimal("100000"),
            receipt_line_id=lines[0].id,
        )
        await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=BILL_DATE,
            lines=[bill_line],
        )
        with pytest.raises(Conflict):
            await payables.create_bill(
                organization_id=sales.id,
                supplier_id=supplier.id,
                kind=BillKind.MATERIALS,
                invoice_date=BILL_DATE,
                lines=[bill_line],
            )

    async def test_the_same_delivery_line_twice_on_one_bill_is_refused(
        self, payables, receiving, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        receipt = await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            lines=[ReceiptLineInput(description="Composite", quantity=Decimal("5"))],
        )
        lines = await receiving.lines(organization_id=sales.id, receipt_id=receipt.id)
        duplicate = BillLineInput(
            description="Composite",
            quantity=Decimal("5"),
            unit_price=Decimal("100000"),
            receipt_line_id=lines[0].id,
        )
        with pytest.raises(ValidationFailed):
            await payables.create_bill(
                organization_id=sales.id,
                supplier_id=supplier.id,
                kind=BillKind.MATERIALS,
                invoice_date=BILL_DATE,
                lines=[duplicate, duplicate],
            )

    async def test_uninvoiced_lines_report_what_has_not_been_billed(
        self, payables, receiving, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        receipt = await receiving.receive(
            organization_id=sales.id,
            supplier_id=supplier.id,
            lines=[
                ReceiptLineInput(description="Composite", quantity=Decimal("5")),
                ReceiptLineInput(description="Gypsum", quantity=Decimal("2")),
            ],
        )
        lines = await receiving.lines(organization_id=sales.id, receipt_id=receipt.id)
        assert (
            len(
                await receiving.uninvoiced_receipt_lines(
                    organization_id=sales.id, supplier_id=supplier.id
                )
            )
            == 2
        )

        await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=BILL_DATE,
            lines=[
                BillLineInput(
                    description=lines[0].description,
                    quantity=lines[0].quantity,
                    unit_price=Decimal("100000"),
                    receipt_line_id=lines[0].id,
                )
            ],
        )
        remaining = await receiving.uninvoiced_receipt_lines(
            organization_id=sales.id, supplier_id=supplier.id
        )
        assert [r.description for r in remaining] == ["Gypsum"]

    async def test_voiding_a_bill_reverses_its_posting(
        self, payables, suppliers, sales, db: AsyncSession
    ) -> None:
        _supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.void_bill(
            organization_id=sales.id, invoice_id=bill.id, reason="Entered twice"
        )
        await db.flush()
        balance = await payables.bill_balance(organization_id=sales.id, invoice_id=bill.id)
        assert balance.total == Decimal("0")

    async def test_voiding_needs_a_reason(self, payables, suppliers, sales) -> None:
        _supplier, bill = await a_registered_bill(payables, suppliers, sales)
        with pytest.raises(ValidationFailed):
            await payables.void_bill(organization_id=sales.id, invoice_id=bill.id, reason="  ")

    async def test_a_paid_bill_cannot_be_voided(self, payables, suppliers, sales) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("500000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
            invoice_id=bill.id,
        )
        with pytest.raises(Conflict):
            await payables.void_bill(
                organization_id=sales.id, invoice_id=bill.id, reason="Too late"
            )

    async def test_registering_is_audited(
        self, payables, suppliers, sales, db: AsyncSession
    ) -> None:
        _supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == sales.id,
                    AuditLog.action == events.SUPPLIER_INVOICE_REGISTERED,
                )
            )
        ).scalar_one()
        assert row.target_label == bill.code


class TestPaying:
    async def test_paying_a_bill_settles_it(self, payables, suppliers, sales) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("500000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
            invoice_id=bill.id,
        )
        balance = await payables.bill_balance(organization_id=sales.id, invoice_id=bill.id)
        assert balance.remaining == Decimal("0.00")

    async def test_a_part_payment_leaves_the_rest_outstanding(
        self, payables, suppliers, sales
    ) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("200000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
            invoice_id=bill.id,
        )
        balance = await payables.bill_balance(organization_id=sales.id, invoice_id=bill.id)
        assert balance.paid == Decimal("200000.00")
        assert balance.remaining == Decimal("300000.00")

    async def test_paying_more_than_is_owed_is_refused(self, payables, suppliers, sales) -> None:
        """Usually it means the bill has already been paid — and silently
        turning the excess into an advance would hide exactly that."""
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        with pytest.raises(ValidationFailed):
            await payables.record_payment(
                organization_id=sales.id,
                supplier_id=supplier.id,
                amount=Decimal("600000"),
                method=SupplierPaymentMethod.BANK,
                paid_on=BILL_DATE,
                invoice_id=bill.id,
            )

    async def test_a_settled_bill_rejects_another_payment(self, payables, suppliers, sales) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("500000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
            invoice_id=bill.id,
        )
        with pytest.raises(Conflict):
            await payables.record_payment(
                organization_id=sales.id,
                supplier_id=supplier.id,
                amount=Decimal("1"),
                method=SupplierPaymentMethod.BANK,
                paid_on=BILL_DATE,
                invoice_id=bill.id,
            )

    async def test_a_draft_bill_cannot_be_paid(self, payables, suppliers, sales) -> None:
        supplier = await a_supplier(suppliers, sales)
        bill = await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=BILL_DATE,
            lines=[
                BillLineInput(
                    description="Composite",
                    quantity=Decimal("1"),
                    unit_price=Decimal("100000"),
                )
            ],
        )
        with pytest.raises(Conflict):
            await payables.record_payment(
                organization_id=sales.id,
                supplier_id=supplier.id,
                amount=Decimal("100000"),
                method=SupplierPaymentMethod.BANK,
                paid_on=BILL_DATE,
                invoice_id=bill.id,
            )

    async def test_a_payment_with_no_bill_is_an_advance(self, payables, suppliers, sales) -> None:
        supplier = await a_supplier(suppliers, sales)
        payment = await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("300000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        assert await payables.unallocated_of(
            organization_id=sales.id, payment_id=payment.id
        ) == Decimal("300000.00")

    async def test_an_advance_can_be_applied_to_a_later_bill(
        self, payables, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        payment = await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("300000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        _s, bill = await a_registered_bill(
            payables, suppliers, sales, total="500000", supplier=supplier
        )
        await payables.apply_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=bill.id,
            amount=Decimal("300000"),
        )
        balance = await payables.bill_balance(organization_id=sales.id, invoice_id=bill.id)
        assert balance.remaining == Decimal("200000.00")
        assert await payables.unallocated_of(
            organization_id=sales.id, payment_id=payment.id
        ) == Decimal("0.00")

    async def test_more_than_the_advance_cannot_be_applied(
        self, payables, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        payment = await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("100000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        _s, bill = await a_registered_bill(
            payables, suppliers, sales, total="500000", supplier=supplier
        )
        with pytest.raises(ValidationFailed):
            await payables.apply_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=bill.id,
                amount=Decimal("200000"),
            )

    async def test_an_advance_cannot_cross_suppliers(self, payables, suppliers, sales) -> None:
        one = await a_supplier(suppliers, sales, name="Depot One")
        payment = await payables.record_payment(
            organization_id=sales.id,
            supplier_id=one.id,
            amount=Decimal("100000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        two = await a_supplier(suppliers, sales, name="Depot Two")
        _s, bill = await a_registered_bill(payables, suppliers, sales, total="100000", supplier=two)
        with pytest.raises(ValidationFailed):
            await payables.apply_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=bill.id,
                amount=Decimal("100000"),
            )

    async def test_reversing_an_allocation_reopens_the_bill(
        self, payables, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        payment = await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("500000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        _s, bill = await a_registered_bill(
            payables, suppliers, sales, total="500000", supplier=supplier
        )
        allocation = await payables.apply_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=bill.id,
            amount=Decimal("500000"),
        )
        await payables.reverse_allocation(
            organization_id=sales.id,
            allocation_id=allocation.id,
            reason="Applied to the wrong bill",
        )
        balance = await payables.bill_balance(organization_id=sales.id, invoice_id=bill.id)
        assert balance.remaining == Decimal("500000.00")
        assert await payables.unallocated_of(
            organization_id=sales.id, payment_id=payment.id
        ) == Decimal("500000.00")

    async def test_a_reversal_is_marked_not_deleted(self, payables, suppliers, sales) -> None:
        supplier = await a_supplier(suppliers, sales)
        payment = await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("100000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        _s, bill = await a_registered_bill(
            payables, suppliers, sales, total="100000", supplier=supplier
        )
        allocation = await payables.apply_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=bill.id,
            amount=Decimal("100000"),
        )
        reversed_row = await payables.reverse_allocation(
            organization_id=sales.id,
            allocation_id=allocation.id,
            reason="Wrong bill",
        )
        assert reversed_row.reversed_at is not None
        assert reversed_row.reversed_reason == "Wrong bill"


class TestWhatIsOwed:
    async def test_a_supplier_balance_adds_up(self, payables, suppliers, sales) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("200000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
            invoice_id=bill.id,
        )
        balance = await payables.supplier_balance(organization_id=sales.id, supplier_id=supplier.id)
        assert balance.billed == Decimal("500000.00")
        assert balance.outstanding == Decimal("300000.00")
        assert balance.advances_available == Decimal("0.00")

    async def test_an_unapplied_payment_shows_as_an_advance(
        self, payables, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("250000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
        )
        balance = await payables.supplier_balance(organization_id=sales.id, supplier_id=supplier.id)
        assert balance.advances_available == Decimal("250000.00")
        assert balance.outstanding == Decimal("0.00")

    async def test_ageing_puts_an_overdue_bill_in_a_bucket(
        self, payables, suppliers, sales
    ) -> None:
        supplier = await a_supplier(suppliers, sales)
        bill = await payables.create_bill(
            organization_id=sales.id,
            supplier_id=supplier.id,
            kind=BillKind.MATERIALS,
            invoice_date=BILL_DATE,
            due_date=BILL_DATE + timedelta(days=10),
            lines=[
                BillLineInput(
                    description="Composite",
                    quantity=Decimal("1"),
                    unit_price=Decimal("400000"),
                )
            ],
        )
        await payables.register_bill(organization_id=sales.id, invoice_id=bill.id)
        rows = await payables.ageing(organization_id=sales.id, as_of=BILL_DATE + timedelta(days=50))
        assert len(rows) == 1
        assert rows[0].days_31_60 == Decimal("400000.00")
        assert rows[0].current == Decimal("0")

    async def test_a_settled_bill_is_not_aged(self, payables, suppliers, sales) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        await payables.record_payment(
            organization_id=sales.id,
            supplier_id=supplier.id,
            amount=Decimal("500000"),
            method=SupplierPaymentMethod.BANK,
            paid_on=BILL_DATE,
            invoice_id=bill.id,
        )
        assert await payables.ageing(organization_id=sales.id) == []


class TestIsolation:
    async def test_another_organization_cannot_read_a_bill(
        self, payables, suppliers, sales, org_b
    ) -> None:
        _supplier, bill = await a_registered_bill(payables, suppliers, sales)
        with pytest.raises(NotFound):
            await payables.get_bill(organization_id=org_b.id, invoice_id=bill.id)

    async def test_another_organization_cannot_pay_a_bill(
        self, payables, suppliers, sales, org_b
    ) -> None:
        supplier, bill = await a_registered_bill(payables, suppliers, sales)
        with pytest.raises(NotFound):
            await payables.record_payment(
                organization_id=org_b.id,
                supplier_id=supplier.id,
                amount=Decimal("1000"),
                method=SupplierPaymentMethod.BANK,
                paid_on=BILL_DATE,
                invoice_id=bill.id,
            )

    async def test_an_unknown_order_is_not_found(self, orders, sales) -> None:
        with pytest.raises(NotFound):
            await orders.get(organization_id=sales.id, order_id=uuid.uuid4())
