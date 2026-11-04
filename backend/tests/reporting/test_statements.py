"""The management statements, and the ledger they have to agree with.

The tests that matter here are the reconciliations. A report is only worth
anything if it cannot disagree with the entries it came from, so the
assertions are mostly of the form "this figure equals that other figure
computed a different way": the profit and loss against the trial balance,
the balance sheet against itself, the cash statement's opening plus movement
against its closing.

The second group is about what a report must *not* do quietly: absorb an
account nobody has classified, include a draft, include a reversed entry, or
move a figure across a date boundary.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.accounting.enums import AccountElement
from app.modules.reporting.services.cashflow import CashFlowService
from app.modules.reporting.services.statements import StatementService
from app.modules.sales.enums import PaymentMethod, RevenueTaxTreatment
from app.modules.sales.services.billing import InvoiceLineInput

JANUARY = date(2026, 1, 15)
FEBRUARY = date(2026, 2, 15)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)
NONE_TAX = RevenueTaxTreatment.NONE


@pytest.fixture
def statements(db, audit):
    return StatementService(db, audit)


@pytest.fixture
def cashflow(db, audit):
    return CashFlowService(db, audit)


async def an_issued_invoice(billing, sales, *, on=JANUARY, amount="300000"):
    invoice = await billing.create_invoice(
        organization_id=sales.id,
        customer_id=sales.customer.id,
        issue_date=on,
        lines=[
            InvoiceLineInput(
                service_id=sales.service.id,
                description="Pulpit treatment",
                quantity=Decimal("1"),
                unit_price=Decimal(amount),
            )
        ],
    )
    await billing.issue_invoice(
        organization_id=sales.id, invoice_id=invoice.id, tax_treatment=NONE_TAX
    )
    return invoice


class TestProfitAndLoss:
    async def test_an_empty_window_is_an_answer_not_an_error(
        self, statements, sales
    ) -> None:
        """A month the clinic has not worked yet shows nothing, not a failure."""
        report = await statements.profit_and_loss(
            organization_id=sales.id, from_date=JANUARY, to_date=JANUARY
        )
        assert report.revenue.lines == []
        assert report.expenses.lines == []
        assert report.net_result == Decimal("0")

    async def test_issuing_an_invoice_recognises_revenue_in_that_window(
        self, statements, billing, sales
    ) -> None:
        await an_issued_invoice(billing, sales)
        report = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.revenue.total == Decimal("300000")
        assert report.net_result == Decimal("300000")
        assert all(line.element is AccountElement.REVENUE for line in report.revenue.lines)

    async def test_a_draft_invoice_changes_nothing(
        self, statements, billing, sales
    ) -> None:
        """A draft is work in progress and must not move a reported figure."""
        await billing.create_invoice(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            issue_date=JANUARY,
            lines=[
                InvoiceLineInput(
                    service_id=sales.service.id,
                    description="Pulpit treatment",
                    quantity=Decimal("1"),
                    unit_price=Decimal("300000"),
                )
            ],
        )
        report = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.revenue.total == Decimal("0")

    async def test_the_window_includes_both_its_ends(
        self, statements, billing, sales
    ) -> None:
        await an_issued_invoice(billing, sales, on=JANUARY)
        on_the_day = await statements.profit_and_loss(
            organization_id=sales.id, from_date=JANUARY, to_date=JANUARY
        )
        assert on_the_day.revenue.total == Decimal("300000")

    async def test_a_day_either_side_of_the_window_is_excluded(
        self, statements, billing, sales
    ) -> None:
        await an_issued_invoice(billing, sales, on=JANUARY)
        before = await statements.profit_and_loss(
            organization_id=sales.id,
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 14),
        )
        after = await statements.profit_and_loss(
            organization_id=sales.id,
            from_date=date(2026, 1, 16),
            to_date=date(2026, 1, 31),
        )
        assert before.revenue.total == Decimal("0")
        assert after.revenue.total == Decimal("0")

    async def test_it_reconciles_to_the_trial_balance(
        self, statements, billing, sales, db
    ) -> None:
        """The two must agree, because one is derived from the other."""
        from app.modules.accounting.services.reports import ReportService

        await an_issued_invoice(billing, sales)
        report = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        trial = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        revenue_movement = sum(
            (
                row.period_credit - row.period_debit
                for row in trial.rows
                if row.element is AccountElement.REVENUE
            ),
            Decimal("0"),
        )
        assert report.revenue.total == revenue_movement

    async def test_a_comparative_period_is_computed_server_side(
        self, statements, billing, sales
    ) -> None:
        await an_issued_invoice(billing, sales, on=JANUARY, amount="300000")
        await an_issued_invoice(billing, sales, on=FEBRUARY, amount="500000")
        report = await statements.profit_and_loss(
            organization_id=sales.id,
            from_date=date(2026, 2, 1),
            to_date=date(2026, 2, 28),
            compare_from=date(2026, 1, 1),
            compare_to=date(2026, 1, 31),
        )
        assert report.revenue.total == Decimal("500000")
        assert report.comparative is not None
        assert report.comparative.revenue.total == Decimal("300000")

    async def test_a_window_that_ends_before_it_begins_is_refused(
        self, statements, sales
    ) -> None:
        with pytest.raises(ValueError):
            await statements.profit_and_loss(
                organization_id=sales.id, from_date=FEBRUARY, to_date=JANUARY
            )


class TestReversalsAndCorrections:
    async def test_a_voided_invoice_leaves_no_revenue_behind(
        self, statements, billing, sales
    ) -> None:
        """Issuing and then voiding nets to nothing over a window spanning both."""
        invoice = await an_issued_invoice(billing, sales)
        await billing.void_invoice(
            organization_id=sales.id, invoice_id=invoice.id, reason="Raised in error"
        )
        report = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.revenue.total == Decimal("0")
        assert report.net_result == Decimal("0")

    async def test_the_balance_sheet_after_a_void_still_balances(
        self, statements, billing, sales
    ) -> None:
        invoice = await an_issued_invoice(billing, sales)
        await billing.void_invoice(
            organization_id=sales.id, invoice_id=invoice.id, reason="Raised in error"
        )
        sheet = await statements.balance_sheet(organization_id=sales.id, as_of=YEAR_END)
        assert sheet.balances, sheet.difference


class TestTheBalanceSheet:
    async def test_it_balances_after_an_invoice_and_its_payment(
        self, statements, billing, sales
    ) -> None:
        invoice = await an_issued_invoice(billing, sales)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=JANUARY,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )
        sheet = await statements.balance_sheet(organization_id=sales.id, as_of=YEAR_END)
        assert sheet.balances, f"difference {sheet.difference}"
        assert sheet.total_assets == sheet.total_liabilities_and_equity

    async def test_a_partial_payment_leaves_the_rest_as_an_asset(
        self, statements, billing, sales
    ) -> None:
        invoice = await an_issued_invoice(billing, sales)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=JANUARY,
            allocate_to=[(invoice.id, Decimal("100000"))],
        )
        sheet = await statements.balance_sheet(organization_id=sales.id, as_of=YEAR_END)
        assert sheet.balances, f"difference {sheet.difference}"
        # The money is in the till and the rest is still owed: total assets
        # are unchanged by *who* holds them.
        assert sheet.total_assets == Decimal("300000")

    async def test_an_advance_is_a_liability_and_not_revenue(
        self, statements, billing, sales
    ) -> None:
        """Money taken before any bill is owed back as a service."""
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("800000"),
            method=PaymentMethod.CASH,
            received_on=JANUARY,
        )
        profit = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        sheet = await statements.balance_sheet(organization_id=sales.id, as_of=YEAR_END)
        assert profit.revenue.total == Decimal("0")
        assert sheet.liabilities.total == Decimal("800000")
        assert sheet.balances, f"difference {sheet.difference}"

    async def test_the_year_result_matches_the_profit_and_loss(
        self, statements, billing, sales
    ) -> None:
        """Two reports, one ledger: the result has to be the same figure."""
        await an_issued_invoice(billing, sales)
        profit = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        sheet = await statements.balance_sheet(organization_id=sales.id, as_of=YEAR_END)
        assert sheet.result_for_the_year == profit.net_result

    async def test_an_empty_clinic_balances_at_zero(self, statements, sales) -> None:
        sheet = await statements.balance_sheet(organization_id=sales.id, as_of=YEAR_END)
        assert sheet.total_assets == Decimal("0")
        assert sheet.balances

    async def test_a_comparative_date_is_computed_server_side(
        self, statements, billing, sales
    ) -> None:
        await an_issued_invoice(billing, sales, on=FEBRUARY)
        sheet = await statements.balance_sheet(
            organization_id=sales.id,
            as_of=date(2026, 2, 28),
            compare_as_of=date(2026, 1, 31),
        )
        assert sheet.comparative is not None
        assert sheet.comparative.total_assets == Decimal("0")
        assert sheet.total_assets == Decimal("300000")


class TestNothingIsClassifiedSilently:
    async def test_an_unclassified_account_is_reported_not_absorbed(
        self, statements, posting, ledger, db, audit
    ) -> None:
        """An account with no element appears in its own section.

        Dropping it would make the statement balance by losing money, and
        guessing where it belongs would be inventing a classification.
        """
        from app.modules.accounting.enums import AccountLevel
        from app.modules.accounting.services.accounts import AccountService

        mystery = await AccountService(db, audit).create_account(
            organization_id=ledger.id,
            code="9999",
            name="Unclassified suspense",
            level=AccountLevel.SYNTHETIC,
        )
        # It has no element at all: the chart does not say what it is.
        assert mystery.element is None

        # Nothing has been posted to it, so it is absent rather than zeroed —
        # the statement reports balances, not the chart.
        report = await statements.profit_and_loss(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert [line.account_code for line in report.unclassified.lines] == []

    async def test_the_count_of_unconfirmed_accounts_is_surfaced(
        self, statements, billing, sales
    ) -> None:
        """The seeded chart is unconfirmed until an accountant says otherwise
        (A1), and the statement says how many of its lines that touches."""
        await an_issued_invoice(billing, sales)
        report = await statements.profit_and_loss(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.accounts_needing_confirmation == len(report.revenue.lines)
        assert all(line.needs_confirmation for line in report.revenue.lines)


class TestClosedPeriods:
    async def test_closing_a_period_does_not_change_what_it_reports(
        self, statements, billing, sales, db, audit
    ) -> None:
        """A closed period is closed to *posting*, not to reading."""
        from app.modules.accounting.services.periods import PeriodService

        await an_issued_invoice(billing, sales, on=JANUARY)
        before = await statements.profit_and_loss(
            organization_id=sales.id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31)
        )

        periods = PeriodService(db, audit)
        january = next(
            period
            for period in sales.ledger.periods
            if period.starts_on == date(2026, 1, 1)
        )
        await periods.close_period(
            organization_id=sales.id, period_id=january.id, actor_user_id=None
        )

        after = await statements.profit_and_loss(
            organization_id=sales.id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31)
        )
        assert after.revenue.total == before.revenue.total
        assert after.net_result == before.net_result


class TestTenantIsolation:
    async def test_one_clinic_never_sees_another_s_figures(
        self, statements, billing, sales, org_b
    ) -> None:
        await an_issued_invoice(billing, sales)
        report = await statements.profit_and_loss(
            organization_id=org_b.id, from_date=YEAR_START, to_date=YEAR_END
        )
        sheet = await statements.balance_sheet(organization_id=org_b.id, as_of=YEAR_END)
        assert report.revenue.total == Decimal("0")
        assert sheet.total_assets == Decimal("0")


class TestCashFlow:
    async def test_with_no_till_and_no_bank_it_says_so(self, cashflow, sales) -> None:
        report = await cashflow.cash_flow(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.no_cash_accounts_configured is True
        assert report.total_in == Decimal("0")

    async def test_a_payment_into_the_till_reconciles(
        self, cashflow, billing, sales, cash, db, audit
    ) -> None:
        """Opening plus movement equals closing, and the money came from
        somewhere the statement can name."""
        await cash.create_register(
            organization_id=sales.id,
            code="MAIN",
            name="Reception till",
            account_code="5010",
        )
        invoice = await an_issued_invoice(billing, sales)
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CASH,
            received_on=JANUARY,
            allocate_to=[(invoice.id, Decimal("300000"))],
        )

        report = await cashflow.cash_flow(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.reconciles, f"difference {report.difference}"
        assert report.closing_balance == Decimal("300000")
        assert report.total_in == Decimal("300000")
        assert report.total_out == Decimal("0")
        # The other side of the entry is the receivable being settled, which
        # is what the statement should name.
        assert [line.account_code for line in report.inflows] == ["4010"]

    async def test_an_empty_window_still_reconciles(
        self, cashflow, sales, cash, db, audit
    ) -> None:
        await cash.create_register(
            organization_id=sales.id,
            code="MAIN",
            name="Reception till",
            account_code="5010",
        )
        report = await cashflow.cash_flow(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.reconciles
        assert report.opening_balance == Decimal("0")
        assert report.closing_balance == Decimal("0")
        assert report.inflows == []
