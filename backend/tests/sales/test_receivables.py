"""Accounts receivable ageing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sales.enums import PaymentMethod
from app.modules.sales.services.receivables import ReceivablesService
from tests.sales.test_billing import issued_invoice

AS_OF = date(2026, 10, 31)


class TestAgeing:
    async def test_an_empty_ledger_ages_to_nothing(self, sales, db: AsyncSession) -> None:
        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        assert report.customers == []
        assert report.total_outstanding == Decimal("0")
        assert {b.label for b in report.bucket_totals} == {
            "current",
            "1-30",
            "31-60",
            "61-90",
            "90+",
        }

    async def test_invoices_fall_into_the_right_buckets(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """Ageing runs from the due date: an invoice is not overdue before it is due."""
        await issued_invoice(
            sales,
            billing,
            amount="100000",
            issue_date=date(2026, 10, 1),
            due_date=date(2026, 11, 30),
        )  # not yet due -> current
        await issued_invoice(
            sales,
            billing,
            amount="200000",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 10, 15),
        )  # 16 days overdue -> 1-30
        await issued_invoice(
            sales,
            billing,
            amount="300000",
            issue_date=date(2026, 6, 1),
            due_date=date(2026, 6, 30),
        )  # 123 days overdue -> 90+
        await db.flush()

        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        buckets = {b.label: b.amount for b in report.bucket_totals}
        assert buckets["current"] == Decimal("100000.00")
        assert buckets["1-30"] == Decimal("200000.00")
        assert buckets["90+"] == Decimal("300000.00")
        assert report.total_outstanding == Decimal("600000.00")

    async def test_a_payment_reduces_the_aged_amount(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued_invoice(
            sales,
            billing,
            amount="300000",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 30),
        )
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=date(2026, 10, 1),
            allocate_to=[(invoice.id, Decimal("100000"))],
        )
        await db.flush()

        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        assert report.total_outstanding == Decimal("200000.00")

    async def test_a_fully_paid_invoice_disappears(self, sales, billing, db: AsyncSession) -> None:
        invoice = await issued_invoice(sales, billing, amount="100000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=date(2026, 10, 2),
            allocate_to=[(invoice.id, Decimal("100000"))],
        )
        await db.flush()

        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        assert report.total_outstanding == Decimal("0")
        assert report.customers == []

    async def test_a_draft_is_not_a_receivable(self, sales, billing, db: AsyncSession) -> None:
        from tests.sales.test_billing import make_invoice

        await make_invoice(sales, billing, amount="500000")
        await db.flush()

        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        assert report.total_outstanding == Decimal("0")

    async def test_an_invoice_issued_after_the_cutoff_is_excluded(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """Ageing as at a date must not include what had not happened yet."""
        await issued_invoice(sales, billing, amount="400000", issue_date=date(2026, 12, 1))
        await db.flush()

        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        assert report.total_outstanding == Decimal("0")

    async def test_customers_are_listed_largest_debt_first(
        self, sales, billing, audit, db: AsyncSession
    ) -> None:
        from app.modules.sales.enums import RevenueTaxTreatment
        from app.modules.sales.services.billing import InvoiceLineInput
        from app.modules.sales.services.catalogue import CatalogueService

        second = await CatalogueService(db, audit).create_customer(
            organization_id=sales.id, code="C-0002", display_name="Another Patient"
        )
        await issued_invoice(sales, billing, amount="100000")

        larger = await billing.create_invoice(
            organization_id=sales.id,
            customer_id=second.id,
            issue_date=date(2026, 10, 1),
            lines=[InvoiceLineInput(description="Treatment", unit_price=Decimal("900000"))],
        )
        await billing.issue_invoice(
            organization_id=sales.id,
            invoice_id=larger.id,
            tax_treatment=RevenueTaxTreatment.NONE,
        )
        await db.flush()

        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=AS_OF)
        assert [c.customer_name for c in report.customers] == [
            "Another Patient",
            "Demo Patient",
        ]
        assert report.customers[0].total_outstanding == Decimal("900000.00")
