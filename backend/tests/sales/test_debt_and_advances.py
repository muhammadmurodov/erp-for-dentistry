"""Patient debt (qarzdorlik) and advances (oldindan to'lov).

The specification's mandatory payment cases, one test each, asserting the
ledger rather than a response code. Two of these are regressions for defects
found by driving the real services and reading the trial balance afterwards:
unwinding an applied advance used to leave the general ledger contradicting
the subledger (ADR-038).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.sales.enums import InvoiceStatus, PaymentMethod, RevenueTaxTreatment
from app.modules.sales.services.billing import InvoiceLineInput

ISSUE_DATE = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)

#: The specification's worked example, in so'm.
TREATMENT = Decimal("2000000")
FIRST_PAYMENT = Decimal("800000")
REMAINING_DEBT = Decimal("1200000")


async def issued(sales, billing, amount: Decimal | str = TREATMENT, **kw):
    """An issued invoice for one treatment."""
    invoice = await billing.create_invoice(
        organization_id=sales.id,
        customer_id=sales.customer.id,
        issue_date=kw.pop("issue_date", ISSUE_DATE),
        lines=[
            InvoiceLineInput(
                description="Canal treatment",
                service_id=sales.service.id,
                unit_price=Decimal(amount),
            )
        ],
        **kw,
    )
    return await billing.issue_invoice(
        organization_id=sales.id,
        invoice_id=invoice.id,
        tax_treatment=RevenueTaxTreatment.NONE,
    )


async def balances(db: AsyncSession, sales) -> dict[str, Decimal]:
    """Each account's closing balance, signed: debit positive."""
    report = await ReportService(db).trial_balance(
        organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
    )
    assert report.is_balanced, "the trial balance must balance after every case"
    return {r.account_code: r.closing_debit - r.closing_credit for r in report.rows}


class TestPayingInFull:
    """Case 1 — a patient pays the whole price."""

    async def test_card_payment_in_full_clears_the_debt(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued(sales, billing)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=TREATMENT,
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, TREATMENT)],
        )
        await db.flush()

        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("0.00")
        assert invoice.status is InvoiceStatus.PAID

        ledger = await balances(db, sales)
        assert ledger["4010"] == Decimal("0.00"), "no receivable left"
        assert ledger["5110.03"] == TREATMENT, "card money sits in transit"
        assert ledger["9030"] == -TREATMENT, "revenue recognised once"


class TestPartialPaymentAndDebt:
    """Cases 2 and 3 — the specification's qarzdorlik example."""

    async def test_a_partial_payment_leaves_the_rest_as_debt(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued(sales, billing)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=FIRST_PAYMENT,
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, FIRST_PAYMENT)],
        )
        await db.flush()

        assert (
            await billing.outstanding(organization_id=sales.id, invoice_id=invoice.id)
            == REMAINING_DEBT
        )
        assert invoice.status is InvoiceStatus.PARTIALLY_PAID

        ledger = await balances(db, sales)
        assert ledger["4010"] == REMAINING_DEBT, "the debt is the receivable"
        assert ledger["9030"] == -TREATMENT, "revenue is the full treatment, not the part paid"

    async def test_the_debt_can_be_cleared_later(self, sales, billing, db: AsyncSession) -> None:
        invoice = await issued(sales, billing)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=FIRST_PAYMENT,
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, FIRST_PAYMENT)],
        )
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=REMAINING_DEBT,
            method=PaymentMethod.CARD,
            received_on=date(2026, 10, 20),
            allocate_to=[(invoice.id, REMAINING_DEBT)],
        )
        await db.flush()

        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("0.00")
        assert invoice.status is InvoiceStatus.PAID
        ledger = await balances(db, sales)
        assert ledger["4010"] == Decimal("0.00")
        assert ledger["9030"] == -TREATMENT, "paying in two parts does not double the revenue"

    async def test_the_balance_is_derived_not_stored(self, sales, billing) -> None:
        """A debt must not be a field anybody can edit."""
        invoice = await issued(sales, billing)
        assert not hasattr(invoice, "balance")
        assert not hasattr(invoice, "outstanding")
        assert not hasattr(invoice, "paid_amount")


class TestAdvances:
    """Cases 4, 5 and 6 — the specification's oldindan to'lov example."""

    async def test_a_deposit_is_a_liability_not_revenue(
        self, sales, billing, db: AsyncSession
    ) -> None:
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        await db.flush()

        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("-500000.00"), "advances from customers, a liability"
        assert "9030" not in ledger, "a deposit is not revenue"

        balance = await billing.customer_balance(
            organization_id=sales.id, customer_id=sales.customer.id
        )
        assert balance.unapplied_credit == Decimal("500000.00")
        assert balance.outstanding == Decimal("0.00")

    async def test_applying_an_advance_leaves_the_rest_owing(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The specification's worked example: 500 000 advance, 1 200 000 bill."""
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=date(2026, 9, 1),
        )
        invoice = await issued(sales, billing, amount="1200000")
        await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("500000"),
        )
        await db.flush()

        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("700000.00")

        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("0.00"), "the advance has been used up"
        assert ledger["4010"] == Decimal("700000.00"), "the remaining debt"

    async def test_an_advance_can_be_applied_in_part(
        self, sales, billing, db: AsyncSession
    ) -> None:
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=date(2026, 9, 1),
        )
        invoice = await issued(sales, billing, amount="200000")
        await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("200000"),
        )
        await db.flush()

        balance = await billing.customer_balance(
            organization_id=sales.id, customer_id=sales.customer.id
        )
        assert balance.unapplied_credit == Decimal("300000.00"), "the rest stays on account"
        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("-300000.00")

    async def test_an_advance_cannot_be_applied_twice(self, sales, billing) -> None:
        """Case 7 of the allocation rules — the same money, twice."""
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=date(2026, 9, 1),
        )
        first = await issued(sales, billing, amount="500000")
        second = await issued(sales, billing, amount="500000")
        await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=first.id,
            amount=Decimal("500000"),
        )
        with pytest.raises(ValidationFailed, match="left to allocate"):
            await billing.allocate(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=second.id,
                amount=Decimal("500000"),
            )

    async def test_an_advance_and_a_debt_coexist_until_applied(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The specification is explicit: do not net these automatically."""
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=date(2026, 9, 1),
        )
        await issued(sales, billing, amount="800000")
        await db.flush()

        balance = await billing.customer_balance(
            organization_id=sales.id, customer_id=sales.customer.id
        )
        assert balance.outstanding == Decimal("800000.00")
        assert balance.unapplied_credit == Decimal("500000.00")


class TestMultipleInvoicesAndMethods:
    """Cases 7 to 9."""

    async def test_one_payment_across_several_invoices(
        self, sales, billing, db: AsyncSession
    ) -> None:
        first = await issued(sales, billing, amount="300000")
        second = await issued(sales, billing, amount="400000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("700000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(first.id, Decimal("300000")), (second.id, Decimal("400000"))],
        )
        await db.flush()

        assert first.status is InvoiceStatus.PAID
        assert second.status is InvoiceStatus.PAID
        ledger = await balances(db, sales)
        assert ledger["4010"] == Decimal("0.00")

    async def test_cash_and_card_together_settle_one_invoice(
        self, sales, billing, cash, till, db: AsyncSession
    ) -> None:
        """Case 9: 400 000 cash + 600 000 card on a 1 000 000 treatment.

        Cash goes through the drawer, so the two halves are separate payment
        records with separate settlement paths — which is the point: one
        invoice, two methods, two reconciliations, one debt of zero.
        """
        invoice = await issued(sales, billing, amount="1000000")
        session = await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=ISSUE_DATE,
            opening_float=Decimal("0"),
        )
        cash_payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("400000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("400000"))],
        )
        await cash.record_payment_receipt(
            organization_id=sales.id,
            session_id=session.id,
            payment_id=cash_payment.id,
            amount=Decimal("400000"),
            description="Cash part",
        )
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("600000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("600000"))],
        )
        await db.flush()

        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("0.00")
        assert invoice.status is InvoiceStatus.PAID

        ledger = await balances(db, sales)
        assert ledger["5010"] == Decimal("400000.00"), "cash in the drawer"
        assert ledger["5110.03"] == Decimal("600000.00"), "card in transit"
        assert ledger["9030"] == Decimal("-1000000.00"), "revenue counted once, not twice"
        assert ledger["4010"] == Decimal("0.00")


class TestOverpaymentRefused:
    """The remaining balance can never go negative."""

    async def test_an_invoice_cannot_be_overpaid(self, sales, billing) -> None:
        invoice = await issued(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        with pytest.raises(ValidationFailed, match=r"only has 300000\.00 outstanding"):
            await billing.allocate(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=invoice.id,
                amount=Decimal("500000"),
            )

    async def test_a_paid_invoice_refuses_further_payment(self, sales, billing) -> None:
        """Specification §8.8 — once settled, no more money goes on it."""
        invoice = await issued(sales, billing, amount="300000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        extra = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        with pytest.raises(Conflict, match="paid"):
            await billing.allocate(
                organization_id=sales.id,
                payment_id=extra.id,
                invoice_id=invoice.id,
                amount=Decimal("100000"),
            )


class TestUnwinding:
    """Regressions for the two defects this work found (ADR-038)."""

    async def test_reversing_an_applied_advance_restores_both_books(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The subledger and the ledger must move together.

        Before the fix, removing the allocation deleted the row but left the
        `6310 Dr / 4010 Cr` entry standing: the invoice reopened as owing
        300 000 while account 4010 reported nothing owed at all.
        """
        invoice = await issued(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        allocation = await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await db.flush()

        await billing.reverse_allocation(
            organization_id=sales.id,
            allocation_id=allocation.id,
            reason="Applied to the wrong invoice",
        )
        await db.flush()

        outstanding = await billing.outstanding(organization_id=sales.id, invoice_id=invoice.id)
        ledger = await balances(db, sales)
        assert outstanding == Decimal("300000.00")
        assert ledger["4010"] == outstanding, "the ledger agrees with the subledger"
        assert ledger["6310"] == Decimal("-300000.00"), "the advance is a liability again"
        assert invoice.status is InvoiceStatus.ISSUED

    async def test_a_reversed_allocation_is_kept_as_history(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        allocation = await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await billing.reverse_allocation(
            organization_id=sales.id,
            allocation_id=allocation.id,
            reason="Applied to the wrong invoice",
        )
        await db.flush()

        live = await billing.list_allocations(organization_id=sales.id, payment_id=payment.id)
        history = await billing.list_allocations(
            organization_id=sales.id, payment_id=payment.id, include_reversed=True
        )
        assert live == []
        assert len(history) == 1
        assert history[0].reversal_reason == "Applied to the wrong invoice"

    async def test_the_same_payment_can_be_reapplied_after_a_reversal(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """Correcting a mistake must not be blocked by the uniqueness rule."""
        invoice = await issued(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        allocation = await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await billing.reverse_allocation(
            organization_id=sales.id, allocation_id=allocation.id, reason="Mistake"
        )
        await db.flush()

        again = await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await db.flush()
        assert again.id != allocation.id
        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("0.00")

    async def test_refunding_an_applied_advance_leaves_no_negative_liability(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """Case 11, and the second defect.

        Before the fix the refund reversed only the payment entry, leaving the
        advance application standing: account 6310 ended up with a **debit**
        balance of 300 000 — a liability owed in reverse, which means nothing.
        """
        invoice = await issued(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await db.flush()

        await billing.refund_payment(
            organization_id=sales.id, payment_id=payment.id, reason="Patient changed their mind"
        )
        await db.flush()

        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("0.00"), "no advance is held any more"
        assert ledger["5110.03"] == Decimal("0.00"), "the money went back"
        assert ledger["4010"] == Decimal("300000.00"), "the invoice is owing again"
        assert ledger["9030"] == Decimal("-300000.00"), "the treatment still happened"
        assert invoice.status is InvoiceStatus.ISSUED

    async def test_a_refund_keeps_the_allocation_history(
        self, sales, billing, db: AsyncSession
    ) -> None:
        invoice = await issued(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        await billing.refund_payment(
            organization_id=sales.id, payment_id=payment.id, reason="Duplicate charge"
        )
        await db.flush()

        history = await billing.list_allocations(
            organization_id=sales.id, payment_id=payment.id, include_reversed=True
        )
        assert len(history) == 1, "the allocation that once existed is still on record"
        assert history[0].reversed_at is not None


class TestWriteOff:
    """A debt given up on is not a billing error."""

    async def test_a_write_off_needs_a_policy_rule(self, sales, billing) -> None:
        """Until `OPEN_QUESTIONS` A7 is answered, this must fail loudly.

        The default policy deliberately carries no `debt_written_off` rule,
        because which account absorbs a bad patient debt is an accountant's
        decision. Guessing one would put a real loss in an invented place.
        """
        invoice = await issued(sales, billing, amount="300000")
        with pytest.raises(NotFound) as exc:
            await billing.write_off_debt(
                organization_id=sales.id,
                invoice_id=invoice.id,
                reason="Patient untraceable",
            )
        assert "debt_written_off" in str(exc.value)

    async def test_a_write_off_requires_a_reason(self, sales, billing) -> None:
        invoice = await issued(sales, billing, amount="300000")
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await billing.write_off_debt(
                organization_id=sales.id, invoice_id=invoice.id, reason="  "
            )

    async def test_a_settled_invoice_has_no_debt_to_write_off(self, sales, billing) -> None:
        invoice = await issued(sales, billing, amount="300000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        with pytest.raises(Conflict, match="no debt to write off"):
            await billing.write_off_debt(
                organization_id=sales.id, invoice_id=invoice.id, reason="Bad debt"
            )


class TestConcurrency:
    """Case 13 — two requests racing to allocate the same payment."""

    async def test_two_allocations_of_one_payment_cannot_both_win(
        self, engine, committed_org
    ) -> None:
        """Two cashiers apply the same advance at the same moment.

        The row lock in `allocate()` is what stops both reading the same
        remaining balance and both succeeding. It runs on real committed
        connections because a check-then-act gap cannot be shown inside one
        shared transaction.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.default_policy import DefaultPolicyService
        from app.modules.accounting.services.periods import PeriodService
        from app.modules.accounting.services.policy import PolicyService
        from app.modules.audit.service import AuditRecorder
        from app.modules.sales.services.billing import BillingService
        from app.modules.sales.services.catalogue import CatalogueService

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        org_id = committed_org.id

        async with factory() as setup:
            recorder = AuditRecorder(setup, actor_label="test")
            await PeriodService(setup, recorder).create_fiscal_year(
                organization_id=org_id,
                code="2026",
                starts_on=YEAR_START,
                ends_on=YEAR_END,
            )
            await AccountService(setup, recorder).seed_statutory_chart(organization_id=org_id)
            policy = await DefaultPolicyService(setup, recorder).seed(
                organization_id=org_id, effective_from=YEAR_START
            )
            await PolicyService(setup, recorder).activate(
                organization_id=org_id, policy_id=policy.id
            )
            catalogue = CatalogueService(setup, recorder)
            customer = await catalogue.create_customer(
                organization_id=org_id, code="C-0001", display_name="Demo Patient"
            )
            service = await catalogue.create_service(
                organization_id=org_id,
                code="S-0001",
                name="Canal treatment",
                price=Decimal("300000"),
                effective_from=YEAR_START,
            )
            billing = BillingService(setup, recorder)
            invoice = await billing.create_invoice(
                organization_id=org_id,
                customer_id=customer.id,
                issue_date=ISSUE_DATE,
                lines=[
                    InvoiceLineInput(
                        description="Canal treatment",
                        service_id=service.id,
                        unit_price=Decimal("300000"),
                    )
                ],
            )
            invoice = await billing.issue_invoice(
                organization_id=org_id,
                invoice_id=invoice.id,
                tax_treatment=RevenueTaxTreatment.NONE,
            )
            payment = await billing.record_payment(
                organization_id=org_id,
                customer_id=customer.id,
                amount=Decimal("300000"),
                method=PaymentMethod.CARD,
                received_on=ISSUE_DATE,
            )
            invoice_id, payment_id = invoice.id, payment.id
            await setup.commit()

        async def attempt() -> str:
            async with factory() as session:
                service = BillingService(session, AuditRecorder(session, actor_label="test"))
                try:
                    await service.allocate(
                        organization_id=org_id,
                        payment_id=payment_id,
                        invoice_id=invoice_id,
                        amount=Decimal("300000"),
                    )
                    await session.commit()
                    return "allocated"
                except Exception as exc:  # which one fails is the point
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(attempt(), attempt())
        assert results.count("allocated") == 1, f"exactly one may succeed, got {results}"

        async with factory() as check:
            billing = BillingService(check, AuditRecorder(check, actor_label="test"))
            allocated = await billing.allocated_to_invoice(
                organization_id=org_id, invoice_id=invoice_id
            )
            assert allocated == Decimal("300000.00"), "settled once, not twice"
