"""Cash sessions and the daily close.

The specification's worked example is `TestTheWorkedExample` below: a patient
pays 300 000 in cash, the system associates it with the right receivable and
raises expected cash; at close an authorised cashier enters the counted
amount; the system compares and records the difference.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, PermissionDenied, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.cash.enums import DepositRoute, SessionStatus
from app.modules.sales.enums import PaymentMethod
from tests.sales.test_billing import issued_invoice

BUSINESS_DATE = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


async def put_cash_in_the_ledger(posting, sales, amount: str) -> None:
    """Post an opening balance so the drawer and the ledger agree.

    A till's opening float is counted toward expected cash but is not posted
    — the money is already there. For a register's first session that only
    holds if the ledger already shows it, so the balance has to get there by
    a journal entry, which is what a real organization would do at cut-over
    (docs/OPEN_QUESTIONS.md question A6).
    """
    from app.modules.accounting.enums import PostingPurpose, SourceKind
    from app.modules.accounting.services.posting import PostingLine, PostingRequest

    await posting.post(
        organization_id=sales.id,
        request=PostingRequest(
            entry_date=BUSINESS_DATE,
            description="Opening cash balance",
            source_kind=SourceKind.OPENING_BALANCE,
            source_id=f"opening-cash-{amount}",
            purpose=PostingPurpose.OPENING_BALANCE,
            reason="Cut-over opening balance",
            lines=[
                PostingLine(account_code="5010", debit=Decimal(amount)),
                PostingLine(account_code="8710", credit=Decimal(amount)),
            ],
        ),
    )


async def open_till(cash, sales, till, *, opening_float: str = "0", posting=None):
    if opening_float != "0":
        assert posting is not None, "a non-zero opening float needs the ledger to show it first"
        await put_cash_in_the_ledger(posting, sales, opening_float)
    return await cash.open_session(
        organization_id=sales.id,
        register_id=till.id,
        business_date=BUSINESS_DATE,
        opening_float=Decimal(opening_float),
    )


class TestOpeningAndClosing:
    async def test_opening_records_the_float_as_a_movement(
        self, cash, sales, posting, till
    ) -> None:
        """Expected cash has one derivation: the movements."""
        session = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert totals.opening_float == Decimal("50000.00")
        assert totals.expected == Decimal("50000.00")

    async def test_only_one_session_per_register_may_be_open(
        self, cash, sales, posting, till
    ) -> None:
        await open_till(cash, sales, till)
        with pytest.raises(Conflict, match="already has an open session"):
            await open_till(cash, sales, till)

    async def test_the_database_refuses_a_second_open_session(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        """A partial unique index, not an application check: two cashiers
        opening the till at the same instant must not both succeed."""
        await open_till(cash, sales, till)
        await db.flush()

        await db.begin_nested()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    """
                    INSERT INTO cash_session
                        (id, organization_id, register_id, business_date, status,
                         currency, opened_at)
                    VALUES (gen_random_uuid(), :org, :reg, :on, 'OPEN', 'UZS', now())
                    """
                ),
                {"org": sales.id, "reg": till.id, "on": BUSINESS_DATE},
            )
            await db.flush()
        await db.rollback()

    async def test_a_session_cannot_be_closed_before_it_is_counted(
        self, cash, sales, posting, till
    ) -> None:
        session = await open_till(cash, sales, till)
        with pytest.raises(Conflict, match="Submit a physical count"):
            await cash.close_session(organization_id=sales.id, session_id=session.id)

    async def test_a_closed_session_takes_no_further_movements(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="10000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("10000"),
        )
        await cash.close_session(organization_id=sales.id, session_id=session.id)
        await db.flush()

        with pytest.raises(Conflict, match="closed"):
            await cash.record_expense(
                organization_id=sales.id,
                session_id=session.id,
                amount=Decimal("100"),
                description="too late",
            )

    async def test_the_database_refuses_a_movement_into_a_closed_session(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="10000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("10000"),
        )
        await cash.close_session(organization_id=sales.id, session_id=session.id)
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO cash_movement
                        (id, organization_id, session_id, kind, amount, occurred_at)
                    VALUES (gen_random_uuid(), :org, :sess, 'RECEIPT', 100, now())
                    """
                ),
                {"org": sales.id, "sess": session.id},
            )
        assert "cannot take further movements" in str(caught.value)
        await db.rollback()

    async def test_a_closed_session_cannot_be_edited(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="10000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("10000"),
        )
        await cash.close_session(organization_id=sales.id, session_id=session.id)
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("UPDATE cash_session SET counted_amount = 1 WHERE id = :id"),
                {"id": session.id},
            )
        assert "closed and cannot be changed" in str(caught.value)
        await db.rollback()


class TestTheWorkedExample:
    """A patient pays 300 000 in cash; the day is closed and reconciled."""

    async def test_a_cash_payment_raises_expected_cash(
        self, cash, sales, posting, billing, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        invoice = await issued_invoice(sales, billing, amount="300000")

        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=BUSINESS_DATE,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        await cash.record_payment_receipt(
            organization_id=sales.id,
            session_id=session.id,
            payment_id=payment.id,
            amount=payment.amount,
        )
        await db.flush()

        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert totals.receipts == Decimal("300000.00")
        assert totals.expected == Decimal("350000.00")
        # The payment settled the receivable, as it should.
        assert await billing.outstanding(
            organization_id=sales.id, invoice_id=invoice.id
        ) == Decimal("0.00")

    async def test_a_matching_count_closes_cleanly(
        self, cash, sales, posting, billing, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=BUSINESS_DATE,
        )
        await cash.record_payment_receipt(
            organization_id=sales.id,
            session_id=session.id,
            payment_id=payment.id,
            amount=payment.amount,
        )
        counted = await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("350000"),
        )
        assert counted.discrepancy == Decimal("0.00")
        assert counted.status is SessionStatus.COUNTED

        closed = await cash.close_session(organization_id=sales.id, session_id=session.id)
        assert closed.status is SessionStatus.CLOSED
        # Nothing to approve, so no approver is recorded.
        assert closed.approved_by_user_id is None

    async def test_the_same_payment_cannot_be_counted_into_the_drawer_twice(
        self, cash, sales, posting, billing, till
    ) -> None:
        session = await open_till(cash, sales, till)
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=BUSINESS_DATE,
        )
        await cash.record_payment_receipt(
            organization_id=sales.id,
            session_id=session.id,
            payment_id=payment.id,
            amount=payment.amount,
        )
        with pytest.raises(Conflict, match="already recorded in a cash session"):
            await cash.record_payment_receipt(
                organization_id=sales.id,
                session_id=session.id,
                payment_id=payment.id,
                amount=payment.amount,
            )


class TestDiscrepancy:
    async def test_a_difference_must_be_explained(self, cash, sales, posting, till) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        with pytest.raises(ValidationFailed, match="must be explained"):
            await cash.submit_count(
                organization_id=sales.id,
                session_id=session.id,
                counted_amount=Decimal("95000"),
            )

    async def test_a_shortage_is_recorded_not_absorbed(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        counted = await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("95000"),
            reason="Short by 5000; no explanation found",
        )
        assert counted.discrepancy == Decimal("-5000.00")

        await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_may_approve=True,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        # The shortage is an expense, not a reduction of revenue.
        assert rows["9430"].closing_debit == Decimal("5000.00")
        assert report.is_balanced

    async def test_an_overage_is_other_income_not_revenue(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("103000"),
            reason="Over by 3000; investigating",
        )
        await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_may_approve=True,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["9390"].closing_credit == Decimal("3000.00")
        assert "9030" not in rows, "an overage is not service revenue"
        assert report.is_balanced

    async def test_closing_with_a_discrepancy_needs_approval_authority(
        self, cash, sales, posting, till
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("95000"),
            reason="short",
        )
        with pytest.raises(PermissionDenied, match="approval authority"):
            await cash.close_session(
                organization_id=sales.id,
                session_id=session.id,
                actor_may_approve=False,
            )

    async def test_the_counter_cannot_approve_their_own_discrepancy(
        self, cash, sales, posting, till, org_a
    ) -> None:
        """The segregation-of-duties pair, enforced where it matters."""
        cashier_id = org_a.members["cashier"][0].id
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("95000"),
            reason="short",
            actor_user_id=cashier_id,
        )
        with pytest.raises(PermissionDenied, match="cannot also approve"):
            await cash.close_session(
                organization_id=sales.id,
                session_id=session.id,
                actor_user_id=cashier_id,
                actor_may_approve=True,
            )

    async def test_someone_else_may_approve_it(
        self, cash, sales, posting, till, org_a, db: AsyncSession
    ) -> None:
        cashier_id = org_a.members["cashier"][0].id
        approver_id = org_a.owner.id

        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("95000"),
            reason="short",
            actor_user_id=cashier_id,
        )
        closed = await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_user_id=approver_id,
            actor_may_approve=True,
        )
        assert closed.status is SessionStatus.CLOSED
        assert closed.approved_by_user_id == approver_id

    async def test_the_owner_may_approve_their_own(self, cash, sales, posting, till, org_a) -> None:
        """A single-owner clinic has nobody else (ADR-018)."""
        owner_id = org_a.owner.id
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("95000"),
            reason="short",
            actor_user_id=owner_id,
        )
        closed = await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_user_id=owner_id,
            actor_may_approve=True,
            actor_is_owner=True,
        )
        assert closed.status is SessionStatus.CLOSED

    async def test_the_close_is_audited_with_the_reason(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        from sqlalchemy import select

        from app.modules.audit import events
        from app.modules.audit.models import AuditLog

        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("95000"),
            reason="Drawer short after a busy morning",
        )
        await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_may_approve=True,
        )
        await db.flush()

        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == sales.id,
                        AuditLog.action == events.CASH_DISCREPANCY_APPROVED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows
        assert rows[0].reason == "Drawer short after a busy morning"


class TestDepositsAndExpenses:
    async def test_a_deposit_posts_to_a_transit_account(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        """The money has left the till but has not reached the bank."""
        session = await open_till(cash, sales, till, posting=posting, opening_float="500000")
        await cash.record_deposit(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("400000"),
            route=DepositRoute.STAFF,
            reference="slip-001",
        )
        await db.flush()

        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert totals.deposits == Decimal("400000.00")
        assert totals.expected == Decimal("100000.00")

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5710.01"].closing_debit == Decimal("400000.00")
        assert "5110.01" not in rows, "the bank has not confirmed it yet"

    async def test_the_collection_route_uses_its_own_transit_account(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="500000")
        await cash.record_deposit(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("200000"),
            route=DepositRoute.COLLECTION,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5710.02"].closing_debit == Decimal("200000.00")

    async def test_a_deposit_cannot_exceed_what_the_till_holds(
        self, cash, sales, posting, till
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        with pytest.raises(ValidationFailed, match="only expected to hold"):
            await cash.record_deposit(
                organization_id=sales.id,
                session_id=session.id,
                amount=Decimal("200000"),
                route=DepositRoute.STAFF,
            )

    async def test_an_expense_reduces_expected_cash_and_posts(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        await cash.record_expense(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("15000"),
            description="Courier for laboratory samples",
        )
        await db.flush()

        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert totals.expenses == Decimal("15000.00")
        assert totals.expected == Decimal("85000.00")

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["9420"].closing_debit == Decimal("15000.00")

    async def test_an_expense_must_say_what_it_was_for(self, cash, sales, posting, till) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        with pytest.raises(ValidationFailed, match="what it was for"):
            await cash.record_expense(
                organization_id=sales.id,
                session_id=session.id,
                amount=Decimal("100"),
                description="   ",
            )


class TestExpectedCashIsDerived:
    async def test_there_is_no_stored_expected_column_to_edit(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        """The guarantee that makes a reconciliation meaningful."""
        columns = (
            (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'cash_session'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "expected_amount" not in columns
        assert "expected" not in columns

    async def test_adding_a_movement_changes_the_expected_balance(
        self, cash, sales, posting, till
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="100000")
        before = await cash.totals(organization_id=sales.id, session_id=session.id)
        await cash.record_expense(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("20000"),
            description="Office supplies",
        )
        after = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert before.expected - after.expected == Decimal("20000.00")


class TestTheDrawerAndTheLedgerAgree:
    """An opening float must already be in the ledger.

    This was a real defect, found by driving a whole cash day in a browser
    and comparing the till against the trial balance: the drawer expected
    135 000 while the ledger showed 80 000, differing by exactly the float.
    Two figures for the same cash, disagreeing silently.
    """

    async def test_a_float_the_ledger_does_not_show_is_refused(self, cash, sales, till) -> None:
        with pytest.raises(ValidationFailed, match="Post an opening balance"):
            await cash.open_session(
                organization_id=sales.id,
                register_id=till.id,
                business_date=BUSINESS_DATE,
                opening_float=Decimal("50000"),
            )

    async def test_a_float_backed_by_the_ledger_is_accepted(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        await db.flush()

        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        cash_row = next(r for r in report.rows if r.account_code == "5010")
        # The drawer and the ledger now say the same thing.
        assert totals.expected == cash_row.closing_debit == Decimal("50000.00")

    async def test_a_zero_float_needs_nothing(self, cash, sales, till) -> None:
        session = await open_till(cash, sales, till)
        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert totals.expected == Decimal("0.00")

    async def test_the_next_session_carries_the_counted_amount_forward(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        """Yesterday's count is today's float. The cash did not move."""
        first = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        await cash.record_expense(
            organization_id=sales.id,
            session_id=first.id,
            amount=Decimal("10000"),
            description="Stationery",
        )
        await cash.submit_count(
            organization_id=sales.id,
            session_id=first.id,
            counted_amount=Decimal("40000"),
        )
        await cash.close_session(organization_id=sales.id, session_id=first.id)
        await db.flush()

        second = await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=date(2026, 10, 2),
            opening_float=Decimal("40000"),
        )
        totals = await cash.totals(organization_id=sales.id, session_id=second.id)
        assert totals.expected == Decimal("40000.00")

    async def test_a_float_that_contradicts_the_previous_count_is_refused(
        self, cash, sales, posting, till, db: AsyncSession
    ) -> None:
        """Otherwise an explained shortage quietly reappears as a float."""
        first = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        await cash.submit_count(
            organization_id=sales.id,
            session_id=first.id,
            counted_amount=Decimal("45000"),
            reason="Short by 5000",
        )
        await cash.close_session(
            organization_id=sales.id, session_id=first.id, actor_may_approve=True
        )
        await db.flush()

        with pytest.raises(ValidationFailed, match="must match it"):
            await cash.open_session(
                organization_id=sales.id,
                register_id=till.id,
                business_date=date(2026, 10, 2),
                opening_float=Decimal("50000"),
            )

    async def test_after_closing_the_ledger_equals_what_was_counted(
        self, cash, sales, billing, posting, till, db: AsyncSession
    ) -> None:
        """The strongest statement this module can make.

        A whole day — float, a payment, an expense, a deposit, a shortage —
        and at the end the ledger's cash account holds exactly what a person
        counted in the drawer. That is what a cash reconciliation is *for*,
        and it is the assertion that would have caught the opening-float
        defect immediately.
        """
        session = await open_till(cash, sales, till, posting=posting, opening_float="50000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=BUSINESS_DATE,
        )
        await cash.record_payment_receipt(
            organization_id=sales.id,
            session_id=session.id,
            payment_id=payment.id,
            amount=payment.amount,
        )
        await cash.record_expense(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("15000"),
            description="Courier for laboratory samples",
        )
        await cash.record_deposit(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("200000"),
            route=DepositRoute.STAFF,
        )

        # Expected: 50 000 + 300 000 - 15 000 - 200 000 = 135 000.
        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert totals.expected == Decimal("135000.00")

        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("130000"),
            reason="Short by 5000; cause not identified",
        )
        await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_may_approve=True,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["5010"].closing_debit == Decimal("130000.00")
        assert rows["9430"].closing_debit == Decimal("5000.00")
        assert rows["5710.01"].closing_debit == Decimal("200000.00")
        assert report.is_balanced
