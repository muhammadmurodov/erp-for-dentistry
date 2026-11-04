"""Giving part of a payment back.

The clinic's commonest refund is not a reversal. The cashier takes 600 000
for a bill of 400 000 and the patient wants the spare 200 000 back: the
payment stays live, part of it is still settling a bill, and a second partial
refund may follow the first. These tests pin that arithmetic down, and pin
down the one case the system refuses — money already applied to an issued
invoice, which cannot be handed back without crediting the invoice.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.sales.enums import PaymentMethod, PaymentStatus
from tests.sales.test_billing import issued_invoice

PAID_ON = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


async def overpaid(sales, billing, *, bill: str = "400000", paid: str = "600000"):
    """A patient who handed over more than the bill: the usual refund case."""
    invoice = await issued_invoice(sales, billing, amount=bill)
    payment = await billing.record_payment(
        organization_id=sales.id,
        customer_id=sales.customer.id,
        amount=Decimal(paid),
        method=PaymentMethod.CASH,
        received_on=PAID_ON,
        allocate_to=[(invoice.id, Decimal(bill))],
    )
    return invoice, payment


class TestWhatMayBeGivenBack:
    async def test_only_the_part_no_bill_has_claimed(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        figures = await billing.refundability(organization_id=sales.id, payment_id=payment.id)
        assert figures.amount == Decimal("600000.00")
        assert figures.allocated == Decimal("400000.00")
        assert figures.refunded == Decimal("0.00")
        assert figures.refundable == Decimal("200000.00")
        assert figures.may_refund is True

    async def test_a_payment_entirely_spent_on_a_bill_has_nothing_to_refund(
        self, sales, billing
    ) -> None:
        """The money is revenue against work the clinic has billed for.

        Handing it back without crediting the invoice would refund the
        patient *and* leave them owing for the same treatment.
        """
        invoice = await issued_invoice(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=PAID_ON,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        figures = await billing.refundability(organization_id=sales.id, payment_id=payment.id)
        assert figures.refundable == Decimal("0.00")
        assert figures.may_refund is False

        with pytest.raises(Conflict, match="nothing left to refund"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("1000"),
                reason="Patient asked for it back",
            )

    async def test_the_message_says_which_instrument_is_needed(self, sales, billing) -> None:
        invoice = await issued_invoice(sales, billing, amount="300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=PAID_ON,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        with pytest.raises(Conflict) as caught:
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("1000"),
                reason="Treatment cancelled",
            )
        assert "credited" in str(caught.value)


class TestPartialRefunds:
    async def test_a_partial_refund_leaves_the_payment_live(
        self, sales, billing, db: AsyncSession
    ) -> None:
        _, payment = await overpaid(sales, billing)
        refund = await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("200000"),
            reason="Patient handed over too much",
        )
        await db.flush()
        assert refund.amount == Decimal("200000.00")
        assert refund.number.startswith("REF-")
        # The payment is not REFUNDED: most of it settled a bill and that
        # settlement stands.
        assert payment.status is PaymentStatus.RECORDED

    async def test_several_partial_refunds_may_follow_one_another(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        for amount in ("50000", "50000", "100000"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal(amount),
                reason="Returned in instalments",
            )
        figures = await billing.refundability(organization_id=sales.id, payment_id=payment.id)
        assert figures.refunded == Decimal("200000.00")
        assert figures.refundable == Decimal("0.00")
        assert figures.may_refund is False
        refunds = await billing.list_refunds(organization_id=sales.id, payment_id=payment.id)
        assert [r.amount for r in refunds] == [
            Decimal("50000.00"),
            Decimal("50000.00"),
            Decimal("100000.00"),
        ]

    async def test_a_refund_beyond_what_is_refundable_is_refused(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        with pytest.raises(ValidationFailed, match=r"Only 200000\.00"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("250000"),
                reason="Too much asked for",
            )

    async def test_the_second_refund_cannot_exceed_what_the_first_left(
        self, sales, billing
    ) -> None:
        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("150000"),
            reason="First instalment",
        )
        with pytest.raises(ValidationFailed, match=r"Only 50000\.00"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("100000"),
                reason="More than is left",
            )

    async def test_a_refund_requires_a_reason(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("1000"),
                reason="   ",
            )

    async def test_a_refund_must_be_positive(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        with pytest.raises(ValidationFailed, match="positive"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("0"),
                reason="Nothing at all",
            )

    async def test_a_fully_refunded_payment_takes_no_partial_refund(self, sales, billing) -> None:
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CARD,
            received_on=PAID_ON,
        )
        await billing.refund_payment(
            organization_id=sales.id, payment_id=payment.id, reason="Taken in error"
        )
        with pytest.raises(Conflict, match="already been refunded"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("1000"),
                reason="Again",
            )

    async def test_a_partly_refunded_payment_cannot_then_be_reversed_in_full(
        self, sales, billing
    ) -> None:
        """Otherwise the refunded part is paid out twice.

        The full-refund path reverses the original entry, which includes
        money that has already been handed back by its own posting.
        """
        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("100000"),
            reason="Part returned",
        )
        with pytest.raises(Conflict, match="already been partly refunded"):
            await billing.refund_payment(
                organization_id=sales.id, payment_id=payment.id, reason="All of it"
            )


class TestTheMoneyAfterwards:
    async def test_refunded_money_is_no_longer_the_patient_s_credit(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        before = await billing.customer_balance(
            organization_id=sales.id, customer_id=sales.customer.id
        )
        assert before.unapplied_credit == Decimal("200000.00")

        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("200000"),
            reason="Handed back",
        )
        after = await billing.customer_balance(
            organization_id=sales.id, customer_id=sales.customer.id
        )
        assert after.unapplied_credit == Decimal("0.00")
        # The bill it settled is untouched: that money was never refunded.
        assert after.outstanding == Decimal("0.00")

    async def test_refunded_money_cannot_then_settle_a_bill(self, sales, billing) -> None:
        """It has left the building. It can no more pay a bill than it can be
        refunded a second time."""
        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("200000"),
            reason="Handed back",
        )
        another = await issued_invoice(sales, billing, amount="150000")
        with pytest.raises(ValidationFailed, match=r"has only 0\.00 left to allocate"):
            await billing.allocate(
                organization_id=sales.id,
                payment_id=payment.id,
                invoice_id=another.id,
                amount=Decimal("150000"),
            )

    async def test_the_clinic_wide_advance_figure_drops(self, sales, billing) -> None:
        _, payment = await overpaid(sales, billing)
        assert await billing.total_unapplied(organization_id=sales.id) == Decimal("200000.00")
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("120000"),
            reason="Most of it back",
        )
        assert await billing.total_unapplied(organization_id=sales.id) == Decimal("80000.00")

    async def test_the_day_s_takings_are_net_of_what_went_back_out(self, sales, billing) -> None:
        """So the cash tile matches the drawer, which is the one figure a
        cashier can check by counting."""
        _, payment = await overpaid(sales, billing)
        takings = await billing.takings_on(organization_id=sales.id, on=PAID_ON)
        assert takings.cash == Decimal("600000.00")

        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("200000"),
            reason="Handed back",
            refunded_on=PAID_ON,
        )
        takings = await billing.takings_on(organization_id=sales.id, on=PAID_ON)
        assert takings.cash == Decimal("400000.00")

    async def test_a_refund_given_on_a_later_day_does_not_change_the_first(
        self, sales, billing
    ) -> None:
        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("200000"),
            reason="Came back for it",
            refunded_on=date(2026, 10, 2),
        )
        assert (await billing.takings_on(organization_id=sales.id, on=PAID_ON)).cash == Decimal(
            "600000.00"
        )


class TestTheLedger:
    async def test_the_refund_discharges_the_liability_and_the_cash_leaves(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """The advance posting, the other way round.

        The money arrived as `advance_received_cash` — debit 5010, credit
        6310, a liability. Handing it over discharges exactly that liability,
        so no new account mapping is invented (ADR-045).
        """
        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("200000"),
            reason="Handed back",
            refunded_on=PAID_ON,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        # 600 000 in, 200 000 out.
        assert rows["5010"].closing_debit == Decimal("400000.00")
        # The advance is gone; the receivable was settled by the other
        # 400 000 and revenue is untouched.
        assert "6310" not in rows or rows["6310"].closing_credit == Decimal("0.00")
        assert rows["9030"].closing_credit == Decimal("400000.00")
        assert rows["4010"].closing_debit == Decimal("0.00")

    async def test_the_books_still_balance(self, sales, billing, db: AsyncSession) -> None:
        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("75000"),
            reason="Part of it",
            refunded_on=PAID_ON,
        )
        await db.flush()
        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.is_balanced

    async def test_two_partial_refunds_post_two_entries(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """Each refund is its own business event, keyed on its own id.

        Keyed on the payment, the second refund would have collided with the
        first and been refused as a duplicate posting.
        """
        _, payment = await overpaid(sales, billing)
        for amount in ("100000", "100000"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal(amount),
                reason="In two halves",
                refunded_on=PAID_ON,
            )
        await db.flush()
        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5010"].closing_debit == Decimal("400000.00")


class TestTheAuditRecord:
    async def test_a_refund_is_audited_with_its_reason_and_what_is_left(
        self, sales, billing, db: AsyncSession
    ) -> None:
        from sqlalchemy import select

        from app.modules.audit.models import AuditLog

        _, payment = await overpaid(sales, billing)
        await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("150000"),
            reason="Patient overpaid in cash",
        )
        await db.flush()

        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == sales.id,
                        AuditLog.action == "sales.refund.approved",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        event = rows[0]
        assert event.reason == "Patient overpaid in cash"
        assert event.new_values["partial"] is True
        assert str(event.new_values["remaining_refundable"]) == "50000.00"

    async def test_self_approval_is_recorded_on_the_refund_itself(
        self, sales, billing, db: AsyncSession
    ) -> None:
        """This is the list a reviewer reads, so the flag lives here too."""
        _, payment = await overpaid(sales, billing)
        refund = await billing.refund_advance(
            organization_id=sales.id,
            payment_id=payment.id,
            amount=Decimal("1000"),
            reason="Small change back",
            self_approved=True,
        )
        await db.flush()
        assert refund.self_approved is True


class TestTwoCashiersAtOnce:
    async def test_the_same_refund_asked_for_twice_runs_out(self, sales, billing) -> None:
        """A double-click does not hand the money over twice.

        There is nothing to make the second request idempotent — two refunds
        of 50 000 are a legitimate thing to want — so the protection is the
        refundable cap, which the second request runs into once the first has
        taken everything.
        """
        _, payment = await overpaid(sales, billing)
        for expected in (True, False):
            figures = await billing.refundability(organization_id=sales.id, payment_id=payment.id)
            assert figures.may_refund is expected
            if not expected:
                break
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("200000"),
                reason="Handed back",
            )
        with pytest.raises(Conflict, match="nothing left to refund"):
            await billing.refund_advance(
                organization_id=sales.id,
                payment_id=payment.id,
                amount=Decimal("200000"),
                reason="Handed back again",
            )

    async def test_two_refunds_racing_cannot_both_take_the_whole_advance(
        self, engine, committed_org
    ) -> None:
        """The row lock in `refund_advance` is what stops them.

        On real committed connections, because a check-then-act gap cannot be
        shown inside one shared transaction. Both cashiers read 200 000
        refundable; only one may hand it over.
        """
        import asyncio

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
            customer = await CatalogueService(setup, recorder).create_customer(
                organization_id=org_id, code="C-0001", display_name="Demo Patient"
            )
            payment = await BillingService(setup, recorder).record_payment(
                organization_id=org_id,
                customer_id=customer.id,
                amount=Decimal("200000"),
                method=PaymentMethod.CARD,
                received_on=PAID_ON,
            )
            payment_id = payment.id
            await setup.commit()

        async def attempt(who: str) -> str:
            async with factory() as session:
                service = BillingService(session, AuditRecorder(session, actor_label=who))
                try:
                    await service.refund_advance(
                        organization_id=org_id,
                        payment_id=payment_id,
                        amount=Decimal("200000"),
                        reason=f"Refunded by {who}",
                        refunded_on=PAID_ON,
                    )
                    await session.commit()
                    return "refunded"
                except Exception as exc:  # which one fails is the point
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(attempt("cashier-one"), attempt("cashier-two"))
        assert results.count("refunded") == 1, f"exactly one may succeed, got {results}"

        async with factory() as check:
            billing = BillingService(check, AuditRecorder(check, actor_label="test"))
            figures = await billing.refundability(organization_id=org_id, payment_id=payment_id)
            assert figures.refunded == Decimal("200000.00"), "handed over once, not twice"
            assert figures.refundable == Decimal("0.00")
