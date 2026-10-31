"""Invoicing, payments, allocation and the rules that keep them honest.

The specification's payment invariants, one section each:

* a partial payment reduces the receivable,
* an advance does not automatically become earned revenue,
* a payment cannot be applied twice,
* refunds follow a reversal and approval process.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.sales.enums import (
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    RevenueTaxTreatment,
)
from app.modules.sales.services.billing import InvoiceLineInput

ISSUE_DATE = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)
NONE_TAX = RevenueTaxTreatment.NONE


async def make_invoice(sales, billing, *, amount: str = "300000", **overrides):
    """A one-line invoice at an explicit price."""
    return await billing.create_invoice(
        organization_id=sales.id,
        customer_id=sales.customer.id,
        issue_date=overrides.pop("issue_date", ISSUE_DATE),
        lines=[
            InvoiceLineInput(
                description="Pulpit treatment",
                service_id=sales.service.id,
                unit_price=Decimal(amount),
            )
        ],
        **overrides,
    )


async def issued_invoice(sales, billing, *, amount: str = "300000", **overrides):
    invoice = await make_invoice(sales, billing, amount=amount, **overrides)
    return await billing.issue_invoice(
        organization_id=sales.id, invoice_id=invoice.id, tax_treatment=NONE_TAX
    )


class TestInvoiceLifecycle:
    async def test_a_draft_has_no_ledger_effect(self, sales, billing, db: AsyncSession) -> None:
        """Preparing an invoice is not recognising revenue."""
        await make_invoice(sales, billing)
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.rows == []

    async def test_issuing_recognises_revenue_and_creates_a_receivable(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued_invoice(sales, billing)
        await db.flush()
        assert invoice.status is InvoiceStatus.ISSUED

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["4010"].closing_debit == Decimal("300000.00")
        assert rows["9030"].closing_credit == Decimal("300000.00")
        assert report.is_balanced

    async def test_an_invoice_cannot_be_issued_twice(self, sales, billing) -> None:
        invoice = await issued_invoice(sales, billing)
        with pytest.raises(Conflict, match="cannot be issued again"):
            await billing.issue_invoice(
                organization_id=sales.id,
                invoice_id=invoice.id,
                tax_treatment=NONE_TAX,
            )

    async def test_issuing_without_declaring_tax_treatment_is_refused(self, sales, billing) -> None:
        """The system refuses to guess an unresolved accounting question."""
        invoice = await make_invoice(sales, billing)
        with pytest.raises(ValidationFailed, match="has not declared how revenue is taxed"):
            await billing.issue_invoice(
                organization_id=sales.id,
                invoice_id=invoice.id,
                tax_treatment=RevenueTaxTreatment.UNDECLARED,
            )

    async def test_vat_inclusive_pricing_refuses_rather_than_inventing_a_rate(
        self, sales, billing
    ) -> None:
        invoice = await make_invoice(sales, billing)
        with pytest.raises(ValidationFailed, match="Nothing is guessed"):
            await billing.issue_invoice(
                organization_id=sales.id,
                invoice_id=invoice.id,
                tax_treatment=RevenueTaxTreatment.VAT_INCLUSIVE,
            )

    async def test_the_price_list_supplies_the_unit_price(self, sales, billing) -> None:
        invoice = await billing.create_invoice(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            issue_date=ISSUE_DATE,
            lines=[InvoiceLineInput(description="Treatment", service_id=sales.service.id)],
        )
        assert invoice.total == Decimal("300000.00")

    async def test_a_service_with_no_price_on_that_date_is_refused(
        self, sales, billing, audit, db: AsyncSession
    ) -> None:
        from app.modules.sales.services.catalogue import CatalogueService

        unpriced = await CatalogueService(db, audit).create_service(
            organization_id=sales.id, code="S-NOPRICE", name="Unpriced"
        )
        with pytest.raises(ValidationFailed, match="has no price on"):
            await billing.create_invoice(
                organization_id=sales.id,
                customer_id=sales.customer.id,
                issue_date=ISSUE_DATE,
                lines=[InvoiceLineInput(description="x", service_id=unpriced.id)],
            )

    async def test_a_line_copies_the_price_so_later_changes_do_not_restate_it(
        self, sales, billing, audit, db: AsyncSession
    ) -> None:
        from app.modules.sales.services.catalogue import CatalogueService

        invoice = await issued_invoice(sales, billing)
        await CatalogueService(db, audit).set_price(
            organization_id=sales.id,
            service_id=sales.service.id,
            amount=Decimal("500000"),
            effective_from=date(2026, 11, 1),
        )
        await db.flush()

        lines = await billing.list_lines(organization_id=sales.id, invoice_id=invoice.id)
        assert lines[0].unit_price == Decimal("300000.00")
        assert invoice.total == Decimal("300000.00")

    async def test_issued_amounts_cannot_be_edited(self, sales, billing, db: AsyncSession) -> None:
        invoice = await issued_invoice(sales, billing)
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("UPDATE invoice SET total = 1 WHERE id = :id"), {"id": invoice.id}
            )
        assert "cannot be changed" in str(caught.value)
        await db.rollback()

    async def test_issued_lines_cannot_be_edited(self, sales, billing, db: AsyncSession) -> None:
        invoice = await issued_invoice(sales, billing)
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("UPDATE invoice_line SET line_total = 1 WHERE invoice_id = :id"),
                {"id": invoice.id},
            )
        assert "cannot be edited" in str(caught.value)
        await db.rollback()

    async def test_voiding_reverses_the_posting(self, sales, billing, db: AsyncSession) -> None:
        invoice = await issued_invoice(sales, billing)
        await billing.void_invoice(
            organization_id=sales.id,
            invoice_id=invoice.id,
            reason="Raised against the wrong patient",
        )
        await db.flush()

        assert invoice.status is InvoiceStatus.VOID
        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["4010"].closing_debit == Decimal("0")
        assert rows["9030"].closing_credit == Decimal("0")
        assert report.is_balanced

    async def test_voiding_requires_a_reason(self, sales, billing) -> None:
        invoice = await issued_invoice(sales, billing)
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await billing.void_invoice(organization_id=sales.id, invoice_id=invoice.id, reason="  ")

    async def test_an_invoice_with_money_on_it_cannot_be_voided(self, sales, billing) -> None:
        """Unapplying somebody's payment is a separate, deliberate act."""
        invoice = await issued_invoice(sales, billing)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("100000"))],
        )
        with pytest.raises(Conflict, match="allocated to it"):
            await billing.void_invoice(
                organization_id=sales.id, invoice_id=invoice.id, reason="changed my mind"
            )


class TestPartialPayment:
    async def test_a_partial_payment_reduces_the_receivable(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued_invoice(sales, billing, amount="300000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("120000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("120000"))],
        )
        await db.flush()

        assert invoice.status is InvoiceStatus.PARTIALLY_PAID
        outstanding = await billing.outstanding(organization_id=sales.id, invoice_id=invoice.id)
        assert outstanding == Decimal("180000.00")

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["4010"].closing_debit == Decimal("180000.00")
        assert rows["5010"].closing_debit == Decimal("120000.00")
        # Revenue is unaffected by collection — it was recognised at issue.
        assert rows["9030"].closing_credit == Decimal("300000.00")

    async def test_paying_the_rest_marks_it_paid(self, sales, billing) -> None:
        invoice = await issued_invoice(sales, billing)
        for amount in ("120000", "180000"):
            await billing.record_payment(
                organization_id=sales.id,
                customer_id=sales.customer.id,
                amount=Decimal(amount),
                method=PaymentMethod.CASH,
                received_on=ISSUE_DATE,
                allocate_to=[(invoice.id, Decimal(amount))],
            )
        assert invoice.status is InvoiceStatus.PAID
        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("0.00")

    async def test_one_payment_can_settle_several_invoices(self, sales, billing) -> None:
        first = await issued_invoice(sales, billing, amount="100000")
        second = await issued_invoice(sales, billing, amount="150000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("250000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[
                (first.id, Decimal("100000")),
                (second.id, Decimal("150000")),
            ],
        )
        assert first.status is InvoiceStatus.PAID
        assert second.status is InvoiceStatus.PAID

    async def test_auto_allocation_takes_the_oldest_first(self, sales, billing) -> None:
        older = await issued_invoice(sales, billing, amount="100000", issue_date=date(2026, 3, 1))
        newer = await issued_invoice(sales, billing, amount="100000", issue_date=date(2026, 9, 1))
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("150000"),
            method=PaymentMethod.CASH,
            received_on=date(2026, 10, 1),
            auto_allocate=True,
        )
        assert older.status is InvoiceStatus.PAID
        assert newer.status is InvoiceStatus.PARTIALLY_PAID


class TestOverAllocation:
    async def test_allocating_more_than_the_payment_is_refused(self, sales, billing) -> None:
        invoice = await issued_invoice(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        with pytest.raises(ValidationFailed, match="left to allocate"):
            await billing.allocate(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=invoice.id,
                amount=Decimal("200000"),
            )

    async def test_allocating_more_than_the_invoice_is_refused(self, sales, billing) -> None:
        invoice = await issued_invoice(sales, billing, amount="100000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        with pytest.raises(ValidationFailed, match="outstanding"):
            await billing.allocate(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=invoice.id,
                amount=Decimal("200000"),
            )

    async def test_the_same_payment_cannot_be_applied_twice_to_one_invoice(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """A double-click or a duplicated request cannot settle it twice."""
        invoice = await issued_invoice(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("100000"))],
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    """
                    INSERT INTO payment_allocation
                        (id, organization_id, payment_id, invoice_id, amount)
                    VALUES (gen_random_uuid(), :org, :payment, :invoice, 100000)
                    """
                ),
                {"org": sales.id, "payment": payment.id, "invoice": invoice.id},
            )
            await db.flush()
        await db.rollback()

    async def test_the_database_refuses_over_allocation_without_the_service(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The service checks under a lock; the trigger is the guarantee."""
        invoice = await issued_invoice(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO payment_allocation
                        (id, organization_id, payment_id, invoice_id, amount)
                    VALUES (gen_random_uuid(), :org, :payment, :invoice, 250000)
                    """
                ),
                {"org": sales.id, "payment": payment.id, "invoice": invoice.id},
            )
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "over-allocated" in str(caught.value)
        await db.rollback()


class TestAdvances:
    async def test_an_advance_is_a_liability_not_revenue(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The specification's rule: an advance does not become earned revenue."""
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("1000000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5010"].closing_debit == Decimal("1000000.00")
        assert rows["6310"].closing_credit == Decimal("1000000.00")
        assert "9030" not in rows, "an advance must not be recognised as revenue"
        assert report.is_balanced

    async def test_applying_an_advance_moves_it_from_liability_to_receivable(
        self, sales, billing, db: AsyncSession
    ) -> None:
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=date(2026, 9, 1),
        )
        invoice = await issued_invoice(sales, billing, amount="300000")
        await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["6310"].closing_credit == Decimal("0")
        assert rows["4010"].closing_debit == Decimal("0")
        assert rows["9030"].closing_credit == Decimal("300000.00")
        assert rows["5010"].closing_debit == Decimal("300000.00")
        assert report.is_balanced

    async def test_a_part_advance_part_settlement_posts_one_entry(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """One movement of money is one journal entry, with three lines."""
        invoice = await issued_invoice(sales, billing, amount="100000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("250000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("100000"))],
        )
        await db.flush()

        from app.modules.accounting.enums import PostingPurpose, SourceKind

        entries = await billing._posting.find_by_source(
            organization_id=sales.id,
            source_kind=SourceKind.PAYMENT,
            source_id=str(payment.id),
            purpose=PostingPurpose.PAYMENT,
        )
        assert len(entries) == 1
        lines = await billing._posting.get_lines(organization_id=sales.id, entry_id=entries[0].id)
        assert len(lines) == 3

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5010"].closing_debit == Decimal("250000.00")
        assert rows["6310"].closing_credit == Decimal("150000.00")
        assert rows["4010"].closing_debit == Decimal("0")

    async def test_customer_balance_separates_debt_from_credit(
        self, sales, billing, db: AsyncSession
    ) -> None:
        await issued_invoice(sales, billing, amount="100000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("250000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            auto_allocate=True,
        )
        await db.flush()

        balance = await billing.customer_balance(
            organization_id=sales.id, customer_id=sales.customer.id
        )
        assert balance.invoiced == Decimal("100000.00")
        assert balance.paid == Decimal("250000.00")
        assert balance.outstanding == Decimal("0.00")
        assert balance.unapplied_credit == Decimal("150000.00")


class TestRefund:
    async def test_a_refund_reverses_the_posting(self, sales, billing, db: AsyncSession) -> None:
        invoice = await issued_invoice(sales, billing)
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=payment.id,
            reason="Treatment cancelled at the patient's request",
        )
        await db.flush()

        assert payment.status is PaymentStatus.REFUNDED
        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5010"].closing_debit == Decimal("0")
        # The invoice is unpaid again, so the receivable comes back.
        assert rows["4010"].closing_debit == Decimal("300000.00")
        assert report.is_balanced
        assert invoice.status is InvoiceStatus.ISSUED

    async def test_a_refund_requires_a_reason(self, sales, billing) -> None:
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await billing.refund_payment(
                organization_id=sales.id, payment_id=payment.id, reason=" "
            )

    async def test_a_payment_cannot_be_refunded_twice(self, sales, billing) -> None:
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        await billing.refund_payment(
            organization_id=sales.id, payment_id=payment.id, reason="first"
        )
        with pytest.raises(Conflict, match="already been refunded"):
            await billing.refund_payment(
                organization_id=sales.id, payment_id=payment.id, reason="second"
            )

    async def test_a_posted_payment_amount_cannot_be_edited(
        self, sales, billing, db: AsyncSession
    ) -> None:
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("UPDATE payment SET amount = 999 WHERE id = :id"),
                {"id": payment.id},
            )
        assert "cannot be changed" in str(caught.value)
        await db.rollback()


class TestCardPayments:
    async def test_a_card_payment_lands_in_the_settlement_transit_account(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The acquirer owes the clinic until it settles — that is the transit
        account's whole purpose, and a non-zero balance there is the list of
        unsettled card payments."""
        invoice = await issued_invoice(sales, billing, amount="500000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("500000"))],
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5110.03"].closing_debit == Decimal("500000.00")
        assert "5010" not in rows, "a card payment is not cash in hand"
        assert rows["9030"].closing_credit == Decimal("500000.00")


class TestTenantIsolation:
    async def test_another_organizations_invoice_is_not_found(
        self, sales, billing, org_b, audit, db: AsyncSession
    ) -> None:
        from app.modules.sales.services.billing import BillingService

        invoice = await issued_invoice(sales, billing)
        other = BillingService(db, audit)
        with pytest.raises(NotFound):
            await other.get_invoice(organization_id=org_b.id, invoice_id=invoice.id)
