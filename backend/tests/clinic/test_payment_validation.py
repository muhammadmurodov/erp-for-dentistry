"""Payment validation and the automatic remaining balance.

The specification's four scenarios, its eleven mandatory behaviours and its
UI guarantees, asserted against the real services and the real ledger. The
treatment is priced at 1 000 000 UZS throughout.

What these tests are really defending is one sentence: **the remaining
balance is computed, never stored and never accepted from a client.** A test
that only checked a response code would pass against a system that let a
cashier type the balance in, so each case here asserts the derived figure,
and several assert the trial balance behind it.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.clinic.enums import SettlementStatus
from app.modules.clinic.schemas import SettlementRequest
from app.modules.sales.enums import InvoiceStatus, PaymentMethod

#: Kept in step with `conftest.py`, which the fixtures use. Duplicated rather
#: than imported because the test directory is not a package.
TREATMENT = Decimal("1000000")
ISSUE_DATE = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)

FULL = TREATMENT
HALF_CASH = Decimal("400000")
HALF_CARD = Decimal("600000")


async def balances(db: AsyncSession, sales) -> dict[str, Decimal]:
    """Each account's closing balance, signed: debit positive."""
    report = await ReportService(db).trial_balance(
        organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
    )
    assert report.is_balanced, "the trial balance must balance after every case"
    return {r.account_code: r.closing_debit - r.closing_credit for r in report.rows}


class TestTheRemainingBalanceIsDerived:
    """Behaviour 1 and 3: computed automatically, never editable."""

    async def test_an_unpaid_bill_shows_the_whole_price_outstanding(
        self, sales, settlement, bill
    ) -> None:
        invoice = await bill()
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)

        assert view.treatment_total == FULL
        assert view.paid == Decimal("0.00")
        assert view.remaining == FULL
        assert view.status is SettlementStatus.UNPAID
        assert view.accepts_payment is True
        assert view.max_payable == FULL

    async def test_the_request_has_no_field_for_the_remaining_balance(self) -> None:
        """Behaviour 3, enforced by the shape of the request itself.

        A read-only field in the UI is a convention. A request schema with no
        such field is a guarantee: there is nowhere for an edited balance to
        arrive, whether the invoice is partly paid or not.
        """
        fields = set(SettlementRequest.model_fields)
        assert "remaining" not in fields
        assert "remaining_balance" not in fields
        assert "total" not in fields
        assert fields >= {"cash", "card", "advances", "request_key"}

    async def test_the_balance_follows_the_allocations_not_a_column(
        self, sales, settlement, bill, open_till, key
    ) -> None:
        """Behaviour 1: total - advances - confirmed allocations, every time.

        Proved by removing the money again: the remaining balance goes back
        up on its own, because nothing cached it.
        """
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=Decimal("250000"),
            request_key=key(),
        )
        assert (
            await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        ).remaining == Decimal("750000")

        allocations = await settlement._billing.list_allocations(
            organization_id=sales.id, invoice_id=invoice.id
        )
        await settlement._billing.reverse_allocation(
            organization_id=sales.id,
            allocation_id=allocations[0].id,
            reason="Receipt voided at the patient's request",
        )
        after = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert after.remaining == FULL, "a reversed allocation is not confirmed money"
        assert after.status is SettlementStatus.UNPAID
        assert after.accepts_payment is True, "behaviour 6: the fields re-enable"


class TestScenarioOneFullCash:
    """1 000 000 cash, 0 card, remaining 0, fully paid."""

    async def test_cash_in_full_settles_the_bill(
        self, sales, settlement, bill, open_till, key, db: AsyncSession
    ) -> None:
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=FULL,
            request_key=key(),
        )

        assert result.view.remaining == Decimal("0.00")
        assert result.view.status is SettlementStatus.FULLY_PAID
        assert result.view.is_fully_paid
        assert result.view.paid_cash == FULL
        assert result.view.paid_card == Decimal("0.00")
        assert result.view.accepts_payment is False, "behaviour 2: actions disabled"
        assert result.view.max_payable == Decimal("0.00")
        assert invoice.status is InvoiceStatus.PAID

        ledger = await balances(db, sales)
        assert ledger["5010"] == FULL, "the money is in the drawer"
        assert ledger["4010"] == Decimal("0.00"), "no receivable left"
        assert ledger["9030"] == -FULL, "revenue counted once"

    async def test_the_cash_reaches_the_drawer_not_only_the_ledger(
        self, sales, settlement, bill, open_till, cash, key
    ) -> None:
        """Otherwise the end-of-day count reports a shortage that is not one.

        This is defect C-3: a cash payment that posts to 5010 without a
        movement in the open session makes real money look missing, and the
        variance is written off to 9430 as a loss.
        """
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=FULL,
            request_key=key(),
        )
        totals = await cash.totals(organization_id=sales.id, session_id=open_till.id)
        assert totals.receipts == FULL
        assert totals.expected == FULL, "the drawer expects the money it holds"


class TestScenarioTwoFullCard:
    """0 cash, 1 000 000 card, remaining 0, fully paid."""

    async def test_card_in_full_settles_the_bill(
        self, sales, settlement, bill, key, db: AsyncSession
    ) -> None:
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=FULL,
            request_key=key(),
        )

        assert result.view.remaining == Decimal("0.00")
        assert result.view.status is SettlementStatus.FULLY_PAID
        assert result.view.paid_card == FULL
        assert result.view.accepts_payment is False

        ledger = await balances(db, sales)
        assert ledger["5110.03"] == FULL, "card money sits in transit until settled"
        assert "5010" not in ledger, "nothing went through the drawer"
        assert ledger["4010"] == Decimal("0.00")

    async def test_card_needs_no_open_drawer(self, sales, settlement, bill, key) -> None:
        """A card payment never touches a register, so none is required."""
        invoice = await bill()
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.open_cash_session_id is None

        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=FULL,
            request_key=key(),
        )
        assert result.view.is_fully_paid


class TestScenarioThreeMixedPayment:
    """400 000 cash + 600 000 card, remaining 0, fully paid."""

    async def test_cash_and_card_in_one_submission(
        self, sales, settlement, bill, open_till, key, db: AsyncSession
    ) -> None:
        """One act for the patient, two settlement paths underneath.

        Cash is counted in a drawer; card money waits in transit for the
        bank. They are separate payment records because that is what the
        money does — but the patient made one decision, and the bill is
        checked once against the pair of them.
        """
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=HALF_CASH,
            card=HALF_CARD,
            request_key=key(),
        )

        assert result.view.remaining == Decimal("0.00")
        assert result.view.status is SettlementStatus.FULLY_PAID
        assert result.view.paid_cash == HALF_CASH
        assert result.view.paid_card == HALF_CARD
        assert result.cash_payment_id is not None
        assert result.card_payment_id is not None

        ledger = await balances(db, sales)
        assert ledger["5010"] == HALF_CASH
        assert ledger["5110.03"] == HALF_CARD
        assert ledger["4010"] == Decimal("0.00")
        assert ledger["9030"] == -FULL, "revenue counted once, not twice"

    async def test_the_two_halves_are_checked_together_not_separately(
        self, sales, settlement, bill, open_till, key
    ) -> None:
        """600 000 and 600 000 each fit the bill alone. Together they do not.

        Checking each part against the remaining balance in turn would let
        this through, which is why the check is made once for the whole
        submission before anything is taken.
        """
        invoice = await bill()
        with pytest.raises(ValidationFailed, match="more than"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                cash=HALF_CARD,
                card=HALF_CARD,
                request_key=key(),
            )
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.paid == Decimal("0.00"), "nothing was taken"
        assert view.remaining == FULL


class TestScenarioFourPartialPayment:
    """300 000 cash + 200 000 card, remaining 500 000, partially paid."""

    async def test_a_part_payment_leaves_the_rest_as_debt(
        self, sales, settlement, bill, open_till, key, db: AsyncSession
    ) -> None:
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=Decimal("300000"),
            card=Decimal("200000"),
            request_key=key(),
        )

        assert result.view.remaining == Decimal("500000")
        assert result.view.status is SettlementStatus.PARTIALLY_PAID
        assert result.view.accepts_payment is True, "the rest can still be paid"
        assert result.view.max_payable == Decimal("500000")
        assert invoice.status is InvoiceStatus.PARTIALLY_PAID

        ledger = await balances(db, sales)
        assert ledger["4010"] == Decimal("500000"), "the qarzdorlik is a receivable"
        assert ledger["9030"] == -FULL, "all the revenue was earned"

    async def test_the_debt_can_be_cleared_later(
        self, sales, settlement, bill, open_till, key
    ) -> None:
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=Decimal("500000"),
            request_key=key(),
        )
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("500000"),
            request_key=key(),
        )
        assert result.view.remaining == Decimal("0.00")
        assert result.view.status is SettlementStatus.FULLY_PAID

    async def test_the_remaining_balance_never_goes_negative(
        self, sales, settlement, bill, open_till, key
    ) -> None:
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=Decimal("900000"),
            request_key=key(),
        )
        with pytest.raises(ValidationFailed, match="more than"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                cash=Decimal("200000"),
                request_key=key(),
            )
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.remaining == Decimal("100000")


class TestAdvanceAllocation:
    """Money the patient left earlier, applied to this bill."""

    async def test_an_advance_shows_as_available_before_it_is_applied(
        self, sales, settlement, bill, advance, db: AsyncSession
    ) -> None:
        await advance("400000")
        invoice = await bill()
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)

        assert view.advances_available == Decimal("400000")
        assert view.remaining == FULL, "an advance is not payment until applied"

        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("-400000"), "a liability, not revenue"

    async def test_applying_an_advance_reduces_the_remaining_balance(
        self, sales, settlement, bill, advance, key, db: AsyncSession
    ) -> None:
        await advance("400000")
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            advances=Decimal("400000"),
            request_key=key(),
        )

        assert result.applied_advances == Decimal("400000")
        assert result.view.remaining == Decimal("600000")
        assert result.view.status is SettlementStatus.PARTIALLY_PAID
        assert result.view.advances_available == Decimal("0.00")

        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("0.00"), "the liability is discharged"
        assert ledger["4010"] == Decimal("600000")

    async def test_an_advance_and_cash_and_card_in_one_submission(
        self, sales, settlement, bill, advance, open_till, key, db: AsyncSession
    ) -> None:
        await advance("200000")
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            advances=Decimal("200000"),
            cash=Decimal("300000"),
            card=Decimal("500000"),
            request_key=key(),
        )

        assert result.view.remaining == Decimal("0.00")
        assert result.view.status is SettlementStatus.FULLY_PAID
        ledger = await balances(db, sales)
        assert ledger["6310"] == Decimal("0.00")
        assert ledger["5010"] == Decimal("300000")
        assert ledger["5110.03"] == Decimal("700000"), "the advance came by card too"
        assert ledger["4010"] == Decimal("0.00")

    async def test_more_advance_than_the_patient_has_is_refused(
        self, sales, settlement, bill, advance, key
    ) -> None:
        await advance("100000")
        invoice = await bill()
        with pytest.raises(ValidationFailed, match="in advances"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                advances=Decimal("400000"),
                request_key=key(),
            )
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.remaining == FULL
        assert view.advances_available == Decimal("100000")

    async def test_the_oldest_advance_is_used_first(
        self, sales, settlement, bill, advance, key
    ) -> None:
        """What a patient expects, and it keeps the liability from ageing."""
        first = await advance("150000")
        second = await advance("150000")
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            advances=Decimal("150000"),
            request_key=key(),
        )
        assert result.advance_payment_ids == [first.id]
        assert second.id not in result.advance_payment_ids


class TestOverpaymentIsRefused:
    """Behaviour 2 and 8, and the UI guarantee of no negative balance."""

    async def test_more_than_the_price_is_refused_before_anything_is_taken(
        self, sales, settlement, bill, open_till, key
    ) -> None:
        invoice = await bill()
        with pytest.raises(ValidationFailed, match="more than") as exc:
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                cash=Decimal("1500000"),
                request_key=key(),
            )
        assert exc.value.details["remaining"] == "1000000.00"
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.paid == Decimal("0.00"), "the message says nothing was taken"

    async def test_a_settled_bill_takes_no_further_payment(
        self, sales, settlement, bill, key
    ) -> None:
        """Behaviour 8: once full payment is confirmed, no more is accepted."""
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=FULL,
            request_key=key(),
        )
        with pytest.raises(Conflict, match="already paid in full"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                card=Decimal("50000"),
                request_key=key(),
            )

    async def test_a_written_off_bill_takes_no_payment(
        self, sales, settlement, billing, bill, key
    ) -> None:
        invoice = await bill()
        try:
            await billing.write_off_debt(
                organization_id=sales.id,
                invoice_id=invoice.id,
                reason="Uncollectable: patient emigrated",
            )
        except (Conflict, NotFound) as exc:
            # Open question A7: which account takes a written-off patient
            # debt is unanswered, so the operation is registered with no
            # policy rule and fails loudly rather than posting to a guess.
            pytest.skip(f"write-off is not postable yet: {exc}")
        with pytest.raises(Conflict, match="written off"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                card=Decimal("50000"),
                request_key=key(),
            )

    async def test_a_draft_bill_takes_no_payment(self, sales, billing, settlement, key) -> None:
        """A draft carries no receivable, so there is nothing to settle."""
        from app.modules.sales.services.billing import InvoiceLineInput

        draft = await billing.create_invoice(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            issue_date=ISSUE_DATE,
            lines=[
                InvoiceLineInput(
                    description="Canal treatment",
                    service_id=sales.service.id,
                    unit_price=FULL,
                )
            ],
        )
        view = await settlement.view(organization_id=sales.id, invoice_id=draft.id)
        assert view.status is SettlementStatus.NOT_PAYABLE
        assert view.accepts_payment is False
        with pytest.raises(Conflict, match="draft"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=draft.id,
                card=FULL,
                request_key=key(),
            )

    async def test_the_database_refuses_an_overpaying_allocation_on_its_own(
        self, sales, settlement, billing, bill, key, db: AsyncSession
    ) -> None:
        """Behaviour 2, at the database transaction level.

        The service check is the one that produces a sentence a cashier can
        read. This is the one that holds when a row reaches the table by some
        other route, and it is what makes the guarantee a guarantee rather
        than a convention: a raw INSERT that would overpay is refused by the
        trigger, under the same locks the service takes.
        """
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("900000"),
            request_key=key(),
        )
        spare = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        with pytest.raises(IntegrityError, match="over-allocated"):
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO payment_allocation "
                        "(organization_id, payment_id, invoice_id, amount) "
                        "VALUES (:org, :payment, :invoice, :amount)"
                    ),
                    {
                        "org": sales.id,
                        "payment": spare.id,
                        "invoice": invoice.id,
                        "amount": Decimal("500000"),
                    },
                )

    async def test_the_database_refuses_spending_a_payment_twice(
        self, sales, settlement, billing, bill, key, db: AsyncSession
    ) -> None:
        """The same guard on the other side: a payment cannot be over-applied."""
        first = await bill("400000")
        second = await bill("400000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("400000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
            allocate_to=[(first.id, Decimal("400000"))],
        )
        with pytest.raises(IntegrityError, match="over-allocated"):
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO payment_allocation "
                        "(organization_id, payment_id, invoice_id, amount) "
                        "VALUES (:org, :payment, :invoice, :amount)"
                    ),
                    {
                        "org": sales.id,
                        "payment": payment.id,
                        "invoice": second.id,
                        "amount": Decimal("400000"),
                    },
                )


class TestDuplicateSubmission:
    """Behaviour 9: the same submission twice must take the money once."""

    async def test_the_same_request_key_replays_the_first_outcome(
        self, sales, settlement, bill, key, db: AsyncSession
    ) -> None:
        """A double-clicked button, a retried request, a refreshed page."""
        invoice = await bill()
        submission = key()
        first = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("600000"),
            request_key=submission,
        )
        second = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("600000"),
            request_key=submission,
        )

        assert first.replayed is False
        assert second.replayed is True, "the second was not a second payment"
        assert second.card_payment_id == first.card_payment_id
        assert second.view.paid == Decimal("600000")
        assert second.view.remaining == Decimal("400000")

        ledger = await balances(db, sales)
        assert ledger["5110.03"] == Decimal("600000"), "charged once"
        assert ledger["4010"] == Decimal("400000")

    async def test_a_reused_key_for_a_different_payment_is_refused(
        self, sales, settlement, bill, key
    ) -> None:
        """Far likelier a client bug than a retry, and worse to answer wrongly.

        Replaying somebody else's receipt would be a more dangerous kind of
        wrong than failing, so a key reused with different amounts fails.
        """
        invoice = await bill()
        submission = key()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("100000"),
            request_key=submission,
        )
        with pytest.raises(Conflict, match="already been used"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                card=Decimal("200000"),
                request_key=submission,
            )

    async def test_two_genuine_payments_both_go_through(self, sales, settlement, bill, key) -> None:
        """Replay protection must not refuse a patient who pays twice over."""
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("400000"),
            request_key=key(),
        )
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("400000"),
            request_key=key(),
        )
        assert result.replayed is False
        assert result.view.paid == Decimal("800000")
        assert result.view.remaining == Decimal("200000")


class TestUnconfirmedMoneyIsNotPayment:
    """Behaviour 7 and 11: only confirmed money counts; history survives."""

    async def test_a_refunded_payment_stops_counting(
        self, sales, settlement, billing, bill, key, db: AsyncSession
    ) -> None:
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=FULL,
            request_key=key(),
        )
        assert result.view.is_fully_paid

        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=result.card_payment_id,
            reason="Terminal charged twice; refunded at the till",
        )
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.remaining == FULL, "a refunded payment is not confirmed money"
        assert view.status is SettlementStatus.UNPAID
        assert view.accepts_payment is True, "behaviour 8: a new balance reopens it"

        ledger = await balances(db, sales)
        assert ledger["4010"] == FULL, "the receivable is back"
        assert ledger["5110.03"] == Decimal("0.00")

    async def test_the_payment_history_survives_a_correction(
        self, sales, settlement, billing, bill, key
    ) -> None:
        """Behaviour 11: corrections leave the trail intact, not tidied away."""
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            card=Decimal("300000"),
            request_key=key(),
        )
        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=result.card_payment_id,
            reason="Wrong card presented",
        )
        history = await settlement.history(organization_id=sales.id, invoice_id=invoice.id)
        assert len(history) == 1
        assert history[0]["amount"] == Decimal("300000.00")
        assert history[0]["reversed_at"] is not None
        assert "refunded" in history[0]["reversal_reason"].lower()

    async def test_a_payment_is_refused_without_an_open_drawer(
        self, sales, settlement, bill, till, key
    ) -> None:
        """Cash with nowhere to go is the C-3 defect. Refuse it, say why."""
        invoice = await bill()
        with pytest.raises(Conflict, match="No cash session is open"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                cash=FULL,
                request_key=key(),
            )
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.remaining == FULL
        assert view.open_cash_session_id is None, "the panel hides the cash field"

    async def test_an_empty_submission_is_refused(self, sales, settlement, bill, key) -> None:
        invoice = await bill()
        with pytest.raises(ValidationFailed, match="Enter an amount"):
            await settlement.settle(
                organization_id=sales.id, invoice_id=invoice.id, request_key=key()
            )

    async def test_a_negative_amount_is_refused(self, sales, settlement, bill, key) -> None:
        """A refund is a different operation, with a different permission."""
        invoice = await bill()
        with pytest.raises(ValidationFailed, match="cannot be negative"):
            await settlement.settle(
                organization_id=sales.id,
                invoice_id=invoice.id,
                card=Decimal("-100000"),
                request_key=key(),
            )


class TestConcurrentPayments:
    """Behaviour 9: two cashiers settling the same bill at the same moment."""

    async def test_two_settlements_of_one_bill_cannot_both_win(self, engine, committed_org) -> None:
        """Two full payments race. Exactly one may succeed.

        On real committed connections, because a check-then-act gap cannot be
        shown inside one shared transaction. Both read a remaining balance of
        1 000 000; the invoice's row lock is what stops both believing their
        payment fits.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.default_policy import DefaultPolicyService
        from app.modules.accounting.services.periods import PeriodService
        from app.modules.accounting.services.policy import PolicyService
        from app.modules.audit.service import AuditRecorder
        from app.modules.clinic.services.settlement import SettlementService
        from app.modules.sales.enums import RevenueTaxTreatment
        from app.modules.sales.services.billing import BillingService, InvoiceLineInput
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
                price=FULL,
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
                        unit_price=FULL,
                    )
                ],
            )
            invoice = await billing.issue_invoice(
                organization_id=org_id,
                invoice_id=invoice.id,
                tax_treatment=RevenueTaxTreatment.NONE,
            )
            invoice_id = invoice.id
            await setup.commit()

        async def attempt(request_key: str) -> str:
            async with factory() as session:
                service = SettlementService(session, AuditRecorder(session, actor_label="test"))
                try:
                    await service.settle(
                        organization_id=org_id,
                        invoice_id=invoice_id,
                        card=FULL,
                        request_key=request_key,
                    )
                    await session.commit()
                    return "settled"
                except Exception as exc:  # which one fails is the point
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(attempt("cashier-one"), attempt("cashier-two"))
        assert results.count("settled") == 1, f"exactly one may win, got {results}"

        async with factory() as check:
            service = SettlementService(check, AuditRecorder(check, actor_label="test"))
            view = await service.view(organization_id=org_id, invoice_id=invoice_id)
            assert view.paid == FULL, "settled once, not twice"
            assert view.remaining == Decimal("0.00")
            assert view.status is SettlementStatus.FULLY_PAID

    async def test_one_submission_retried_concurrently_pays_once(
        self, engine, committed_org
    ) -> None:
        """The same request key from two connections at once.

        The duplicate blocks on the unique index rather than reading a stale
        balance, then finds the committed submission and replays it.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.default_policy import DefaultPolicyService
        from app.modules.accounting.services.periods import PeriodService
        from app.modules.accounting.services.policy import PolicyService
        from app.modules.audit.service import AuditRecorder
        from app.modules.clinic.services.settlement import SettlementService
        from app.modules.sales.enums import RevenueTaxTreatment
        from app.modules.sales.services.billing import BillingService, InvoiceLineInput
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
                organization_id=org_id, code="C-0002", display_name="Demo Patient"
            )
            service = await catalogue.create_service(
                organization_id=org_id,
                code="S-0002",
                name="Canal treatment",
                price=FULL,
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
                        unit_price=FULL,
                    )
                ],
            )
            invoice = await billing.issue_invoice(
                organization_id=org_id,
                invoice_id=invoice.id,
                tax_treatment=RevenueTaxTreatment.NONE,
            )
            invoice_id = invoice.id
            await setup.commit()

        async def attempt() -> str:
            async with factory() as session:
                service = SettlementService(session, AuditRecorder(session, actor_label="test"))
                try:
                    result = await service.settle(
                        organization_id=org_id,
                        invoice_id=invoice_id,
                        card=Decimal("600000"),
                        request_key="double-clicked-once",
                    )
                    await session.commit()
                    return "replayed" if result.replayed else "settled"
                except Exception as exc:
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(attempt(), attempt())
        assert results.count("settled") == 1, f"one real settlement, got {results}"

        async with factory() as check:
            service = SettlementService(check, AuditRecorder(check, actor_label="test"))
            view = await service.view(organization_id=org_id, invoice_id=invoice_id)
            assert view.paid == Decimal("600000"), "charged once"
            assert view.remaining == Decimal("400000")


class TestRefundingCashKeepsTheDrawerHonest:
    """ADR-040 — reversing the ledger is only half of giving money back."""

    async def test_a_cash_refund_leaves_the_drawer_and_the_ledger_agreeing(
        self, sales, settlement, billing, cash, bill, open_till, key, db: AsyncSession
    ) -> None:
        """Found by probing, not by a failing test.

        The reversal took account 5010 to zero while the session went on
        expecting the full amount, so the end-of-day count reported a
        shortage that was not one and real cash was posted to 9430 as a loss.
        It is the C-3 defect in the other direction.
        """
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=FULL,
            request_key=key(),
        )
        taken = await cash.totals(organization_id=sales.id, session_id=open_till.id)
        assert taken.expected == FULL

        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=result.cash_payment_id,
            reason="Treatment abandoned; cash returned at the till",
        )
        await cash.record_payment_refund(
            organization_id=sales.id,
            session_id=open_till.id,
            payment_id=result.cash_payment_id,
            amount=FULL,
        )

        after = await cash.totals(organization_id=sales.id, session_id=open_till.id)
        ledger = await balances(db, sales)
        assert after.refunds == FULL
        assert after.expected == Decimal("0.00"), "the till is empty again"
        assert ledger.get("5010", Decimal("0")) == after.expected, (
            "the drawer and the ledger must agree, or the count invents a loss"
        )
        assert ledger["4010"] == FULL, "the receivable is back"

        # And the bill is payable again, which is behaviour 8: a refund
        # creates a new outstanding balance.
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.remaining == FULL
        assert view.accepts_payment is True

    async def test_the_drawer_cannot_pay_one_refund_out_twice(
        self, sales, settlement, billing, cash, bill, open_till, key
    ) -> None:
        """The uniqueness rule is per direction, not per payment."""
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=FULL,
            request_key=key(),
        )
        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=result.cash_payment_id,
            reason="Treatment abandoned",
        )
        await cash.record_payment_refund(
            organization_id=sales.id,
            session_id=open_till.id,
            payment_id=result.cash_payment_id,
            amount=FULL,
        )
        with pytest.raises(Conflict, match="already been refunded"):
            await cash.record_payment_refund(
                organization_id=sales.id,
                session_id=open_till.id,
                payment_id=result.cash_payment_id,
                amount=FULL,
            )

    async def test_the_receipt_and_the_refund_coexist(
        self, sales, settlement, billing, cash, bill, open_till, key
    ) -> None:
        """Both movements are kept: the trail shows the money in and out."""
        invoice = await bill()
        result = await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=FULL,
            request_key=key(),
        )
        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=result.cash_payment_id,
            reason="Treatment abandoned",
        )
        await cash.record_payment_refund(
            organization_id=sales.id,
            session_id=open_till.id,
            payment_id=result.cash_payment_id,
            amount=FULL,
        )
        movements = await cash.list_movements(organization_id=sales.id, session_id=open_till.id)
        kinds = [m.kind.value for m in movements if m.payment_id == result.cash_payment_id]
        assert sorted(kinds) == ["RECEIPT", "REFUND"]


class TestNothingIsOwedOnABillNobodyMayPay:
    """A remaining balance is what can be asked for, not what is unpaid."""

    async def test_a_voided_invoice_owes_nothing(self, sales, settlement, billing, bill) -> None:
        """Also found by probing: the full price was reported as remaining.

        A voided invoice has been reversed out of the ledger. Reporting its
        unpaid portion put the whole treatment price in a column headed
        "remaining balance" on a bill that nobody may pay.
        """
        invoice = await bill()
        await billing.void_invoice(
            organization_id=sales.id,
            invoice_id=invoice.id,
            reason="Raised against the wrong patient",
        )
        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.status is SettlementStatus.NOT_PAYABLE
        assert view.remaining == Decimal("0.00")
        assert view.treatment_total == FULL, "the price is still visible"
        assert view.accepts_payment is False

    async def test_a_written_off_debt_owes_nothing_collectible(
        self, sales, settlement, billing, bill, open_till, key
    ) -> None:
        """What was given up on is reported as written off, not as owing."""
        invoice = await bill()
        await settlement.settle(
            organization_id=sales.id,
            invoice_id=invoice.id,
            cash=Decimal("400000"),
            request_key=key(),
        )
        try:
            await billing.write_off_debt(
                organization_id=sales.id,
                invoice_id=invoice.id,
                reason="Uncollectable: patient emigrated",
            )
        except (Conflict, NotFound) as exc:  # open question A7
            pytest.skip(f"write-off is not postable yet: {exc}")

        view = await settlement.view(organization_id=sales.id, invoice_id=invoice.id)
        assert view.status is SettlementStatus.WRITTEN_OFF
        assert view.remaining == Decimal("0.00")
        assert view.written_off == Decimal("600000"), "only the part given up on"
        assert view.paid == Decimal("400000"), "what the patient did pay stands"
