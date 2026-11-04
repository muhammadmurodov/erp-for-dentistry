"""Profit and loss, and the balance sheet, derived from posted entries.

**Nothing here stores a figure, and nothing here classifies an account.**
Both statements are built from the same trial balance the accounting module
already computes, so a report can never disagree with the ledger it came
from — if it did, the trial balance would have to be wrong too.

**An account the chart has not classified is shown, not hidden.** The seeded
chart is marked `needs_confirmation` while `OPEN_QUESTIONS.md` A1 is
unanswered, and some accounts may carry no `element` at all. Dropping those
from a statement would make it balance by losing money; guessing where they
belong would be inventing a statutory classification. So they are reported
in their own section, counted, and the statement says plainly whether it
balanced.

**These are management statements, not statutory forms.** The filing formats
and their line mappings are `OPEN_QUESTIONS.md` A2, and nothing here claims
to be one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, quantize
from app.modules.accounting.enums import AccountElement
from app.modules.accounting.services.periods import PeriodService
from app.modules.accounting.services.reports import ReportService, TrialBalanceRow
from app.modules.audit.service import AuditRecorder

#: The earliest date a report will look back to when accumulating a balance.
#: Every posting in this system carries an entry date inside a fiscal year,
#: and no fiscal year can start before the company existed.
BEGINNING_OF_TIME = date(1900, 1, 1)


@dataclass(frozen=True, slots=True)
class StatementLine:
    """One account on a statement, with the figure it contributes.

    `amount` is already signed the way the statement reads it: revenue and
    liabilities are positive when they are credit balances, assets and
    expenses when they are debit balances. The raw debit and credit are kept
    beside it so a reader can always get back to the trial balance.
    """

    account_id: uuid.UUID
    account_code: str
    account_name: str
    element: AccountElement | None
    amount: Decimal
    debit: Decimal
    credit: Decimal
    #: True while the statutory classification of this account is unconfirmed
    #: (`OPEN_QUESTIONS.md` A1). Shown, never silently trusted.
    needs_confirmation: bool


@dataclass(frozen=True, slots=True)
class StatementSection:
    key: str
    lines: list[StatementLine]
    total: Decimal


@dataclass(frozen=True, slots=True)
class ProfitAndLoss:
    """What was earned and what it cost, over a window.

    `comparative` holds the same shape for an earlier window when one was
    asked for, so a reader can see this month against last without the
    screen doing arithmetic the server should do.
    """

    from_date: date
    to_date: date
    currency: str
    revenue: StatementSection
    expenses: StatementSection
    unclassified: StatementSection
    net_result: Decimal
    accounts_needing_confirmation: int
    comparative: ProfitAndLoss | None = None


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    """What the clinic owns and owes on one day.

    `result_for_the_year` is the revenue less the expenses of the open
    fiscal year — the profit that has not yet been closed to equity. Without
    it the statement would not balance, and adding it is arithmetic rather
    than a classification.
    """

    as_of: date
    currency: str
    assets: StatementSection
    liabilities: StatementSection
    equity: StatementSection
    unclassified: StatementSection
    result_for_the_year: Decimal
    total_assets: Decimal
    total_liabilities_and_equity: Decimal
    difference: Decimal
    balances: bool
    accounts_needing_confirmation: int
    fiscal_year_starts_on: date | None
    comparative: BalanceSheet | None = None


@dataclass(frozen=True, slots=True)
class _Balance:
    """One account's net position over whatever window was asked for.

    Built from a trial balance row rather than from the chart, so a
    statement and the trial balance can never disagree about an account.
    """

    row: TrialBalanceRow
    debit: Decimal
    credit: Decimal


class StatementService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._reports = ReportService(session)
        self._periods = PeriodService(session, audit)

    # ------------------------------------------------------ profit and loss

    async def profit_and_loss(
        self,
        *,
        organization_id: uuid.UUID,
        from_date: date,
        to_date: date,
        currency: str = "UZS",
        compare_from: date | None = None,
        compare_to: date | None = None,
    ) -> ProfitAndLoss:
        """Revenue less expenses for the window, from posted lines only.

        An empty window is a real answer: every section comes back empty with
        a net result of zero, rather than an error. A clinic that opens a
        report for a month it has not worked yet should see nothing, not a
        failure.
        """
        if to_date < from_date:
            raise ValueError("A report window ends after it begins")

        balances = await self._movements(
            organization_id=organization_id,
            from_date=from_date,
            to_date=to_date,
            currency=currency,
        )

        revenue_lines: list[StatementLine] = []
        expense_lines: list[StatementLine] = []
        unclassified_lines: list[StatementLine] = []

        for balance in balances:
            element = balance.row.element
            if element is AccountElement.REVENUE:
                # Revenue is earned as a credit, so a credit balance is a
                # positive figure on the statement.
                revenue_lines.append(self._line(balance, amount=balance.credit - balance.debit))
            elif element is AccountElement.EXPENSE:
                expense_lines.append(self._line(balance, amount=balance.debit - balance.credit))
            elif element is None:
                unclassified_lines.append(
                    self._line(balance, amount=balance.debit - balance.credit)
                )

        revenue = self._section("revenue", revenue_lines, currency)
        expenses = self._section("expenses", expense_lines, currency)
        unclassified = self._section("unclassified", unclassified_lines, currency)

        comparative = None
        if compare_from is not None and compare_to is not None:
            comparative = await self.profit_and_loss(
                organization_id=organization_id,
                from_date=compare_from,
                to_date=compare_to,
                currency=currency,
            )

        return ProfitAndLoss(
            from_date=from_date,
            to_date=to_date,
            currency=currency,
            revenue=revenue,
            expenses=expenses,
            unclassified=unclassified,
            net_result=quantize(revenue.total - expenses.total, currency),
            accounts_needing_confirmation=sum(
                1
                for line in (*revenue.lines, *expenses.lines, *unclassified.lines)
                if line.needs_confirmation
            ),
            comparative=comparative,
        )

    # --------------------------------------------------------- balance sheet

    async def balance_sheet(
        self,
        *,
        organization_id: uuid.UUID,
        as_of: date,
        currency: str = "UZS",
        compare_as_of: date | None = None,
    ) -> BalanceSheet:
        """Everything posted up to and including `as_of`.

        The result of the open fiscal year is carried as its own figure
        rather than folded into equity, because until the year is closed
        nobody has posted it there — and a statement that shows it as equity
        would disagree with the ledger it came from.
        """
        balances = await self._cumulative(
            organization_id=organization_id, as_of=as_of, currency=currency
        )

        asset_lines: list[StatementLine] = []
        liability_lines: list[StatementLine] = []
        equity_lines: list[StatementLine] = []
        unclassified_lines: list[StatementLine] = []
        revenue_total = ZERO
        expense_total = ZERO

        year_start = await self._fiscal_year_start(organization_id=organization_id, on=as_of)

        for balance in balances:
            element = balance.row.element
            if element is AccountElement.ASSET:
                asset_lines.append(self._line(balance, amount=balance.debit - balance.credit))
            elif element is AccountElement.LIABILITY:
                liability_lines.append(self._line(balance, amount=balance.credit - balance.debit))
            elif element is AccountElement.EQUITY:
                equity_lines.append(self._line(balance, amount=balance.credit - balance.debit))
            elif element in (AccountElement.REVENUE, AccountElement.EXPENSE):
                continue
            elif element is AccountElement.RESULT:
                equity_lines.append(self._line(balance, amount=balance.credit - balance.debit))
            else:
                unclassified_lines.append(
                    self._line(balance, amount=balance.debit - balance.credit)
                )

        # The year's result, from the movements of the open year only: what
        # earlier years earned was closed into equity when they closed.
        if year_start is not None:
            movements = await self._movements(
                organization_id=organization_id,
                from_date=year_start,
                to_date=as_of,
                currency=currency,
            )
            for balance in movements:
                if balance.row.element is AccountElement.REVENUE:
                    revenue_total += balance.credit - balance.debit
                elif balance.row.element is AccountElement.EXPENSE:
                    expense_total += balance.debit - balance.credit

        assets = self._section("assets", asset_lines, currency)
        liabilities = self._section("liabilities", liability_lines, currency)
        equity = self._section("equity", equity_lines, currency)
        unclassified = self._section("unclassified", unclassified_lines, currency)
        result = quantize(revenue_total - expense_total, currency)

        total_assets = assets.total
        total_claims = quantize(liabilities.total + equity.total + result, currency)
        difference = quantize(total_assets - total_claims - unclassified.total, currency)

        comparative = None
        if compare_as_of is not None:
            comparative = await self.balance_sheet(
                organization_id=organization_id, as_of=compare_as_of, currency=currency
            )

        return BalanceSheet(
            as_of=as_of,
            currency=currency,
            assets=assets,
            liabilities=liabilities,
            equity=equity,
            unclassified=unclassified,
            result_for_the_year=result,
            total_assets=total_assets,
            total_liabilities_and_equity=total_claims,
            difference=difference,
            balances=difference == ZERO,
            accounts_needing_confirmation=sum(
                1
                for line in (
                    *assets.lines,
                    *liabilities.lines,
                    *equity.lines,
                    *unclassified.lines,
                )
                if line.needs_confirmation
            ),
            fiscal_year_starts_on=year_start,
            comparative=comparative,
        )

    # ---------------------------------------------------------------- shared

    async def _movements(
        self,
        *,
        organization_id: uuid.UUID,
        from_date: date,
        to_date: date,
        currency: str,
    ) -> list[_Balance]:
        """What moved in the window, per account, from the trial balance."""
        trial = await self._reports.trial_balance(
            organization_id=organization_id,
            from_date=from_date,
            to_date=to_date,
            currency=currency,
        )
        return [
            _Balance(row=row, debit=row.period_debit, credit=row.period_credit)
            for row in trial.rows
            if not (row.period_debit == ZERO and row.period_credit == ZERO)
        ]

    async def _cumulative(
        self, *, organization_id: uuid.UUID, as_of: date, currency: str
    ) -> list[_Balance]:
        """Everything up to `as_of`, per account.

        The window starts before any posting can exist, so the trial
        balance's closing column *is* the cumulative position.
        """
        trial = await self._reports.trial_balance(
            organization_id=organization_id,
            from_date=BEGINNING_OF_TIME,
            to_date=as_of,
            currency=currency,
        )
        return [
            _Balance(row=row, debit=row.closing_debit, credit=row.closing_credit)
            for row in trial.rows
            if not (row.closing_debit == ZERO and row.closing_credit == ZERO)
        ]

    async def _fiscal_year_start(
        self, *, organization_id: uuid.UUID, on: date
    ) -> date | None:
        """The start of the fiscal year containing `on`, if one is defined."""
        for year in await self._periods.list_fiscal_years(organization_id):
            if year.starts_on <= on <= year.ends_on:
                return year.starts_on
        return None

    @staticmethod
    def _line(balance: _Balance, *, amount: Decimal) -> StatementLine:
        return StatementLine(
            account_id=balance.row.account_id,
            account_code=balance.row.account_code,
            account_name=balance.row.account_name,
            element=balance.row.element,
            amount=amount,
            debit=balance.debit,
            credit=balance.credit,
            needs_confirmation=balance.row.needs_confirmation,
        )

    @staticmethod
    def _section(key: str, lines: list[StatementLine], currency: str) -> StatementSection:
        ordered = sorted(lines, key=lambda line: line.account_code)
        return StatementSection(
            key=key,
            lines=ordered,
            total=quantize(sum((line.amount for line in ordered), ZERO), currency),
        )
