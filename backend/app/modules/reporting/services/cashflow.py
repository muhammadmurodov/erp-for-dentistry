"""Where the money went, between two dates.

**What counts as cash is the clinic's own configuration, not a guess.** A
cash register names the ledger account its drawer belongs to, and a bank
account names the ledger account it is paired with. Those accounts *are* the
clinic's cash. Reading a statutory account range instead — "anything
beginning with 5" — would be inventing a classification, which is exactly
what `OPEN_QUESTIONS.md` A1 forbids while the chart is unconfirmed.

**This is a movement statement, not the statutory cash flow form.** It says
what came in, what went out, and what it was for, and it reconciles opening
plus movement to closing. It does **not** sort those movements into
operating, investing and financing: which method the filing requires is
`OPEN_QUESTIONS.md` A3, and a three-way split nobody has approved would look
like an answer. The screen says so.

Each movement is attributed to the other side of its own journal entry. Most
entries here have exactly two lines, so the attribution is exact. Where an
entry has several non-cash lines the cash is apportioned across them in
proportion to their amounts — arithmetic over the entry's own figures, not a
judgement about what the payment was for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, quantize
from app.modules.accounting.enums import AccountElement
from app.modules.accounting.services.accounts import AccountService
from app.modules.accounting.services.reports import EntryLine, ReportService
from app.modules.audit.service import AuditRecorder
from app.modules.banking.services.accounts import BankAccountService
from app.modules.cash.services.sessions import CashSessionService


@dataclass(frozen=True, slots=True)
class CashAccountRow:
    account_id: uuid.UUID
    account_code: str
    account_name: str
    opening: Decimal
    movement: Decimal
    closing: Decimal


@dataclass(frozen=True, slots=True)
class CashFlowLine:
    """One counter-account, and the cash that moved because of it."""

    account_id: uuid.UUID
    account_code: str
    account_name: str
    element: AccountElement | None
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CashFlow:
    from_date: date
    to_date: date
    currency: str
    opening_balance: Decimal
    closing_balance: Decimal
    total_in: Decimal
    total_out: Decimal
    net_movement: Decimal
    #: opening plus net movement, less closing. Zero, or the report is wrong and
    #: says so rather than hiding it.
    difference: Decimal
    reconciles: bool
    cash_accounts: list[CashAccountRow]
    inflows: list[CashFlowLine]
    outflows: list[CashFlowLine]
    #: True when the clinic has configured no till and no bank account, so
    #: there is nothing this report could be about.
    no_cash_accounts_configured: bool


class CashFlowService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._reports = ReportService(session)
        self._accounts_service = AccountService(session, audit)
        self._banking = BankAccountService(session, audit)
        self._tills = CashSessionService(session, audit)

    async def cash_flow(
        self,
        *,
        organization_id: uuid.UUID,
        from_date: date,
        to_date: date,
        currency: str = "UZS",
    ) -> CashFlow:
        if to_date < from_date:
            raise ValueError("A report window ends after it begins")

        cash_ids = await self._cash_account_ids(organization_id=organization_id)
        if not cash_ids:
            return CashFlow(
                from_date=from_date,
                to_date=to_date,
                currency=currency,
                opening_balance=ZERO,
                closing_balance=ZERO,
                total_in=ZERO,
                total_out=ZERO,
                net_movement=ZERO,
                difference=ZERO,
                reconciles=True,
                cash_accounts=[],
                inflows=[],
                outflows=[],
                no_cash_accounts_configured=True,
            )

        trial = await self._reports.trial_balance(
            organization_id=organization_id,
            from_date=from_date,
            to_date=to_date,
            currency=currency,
        )
        accounts = await self._accounts(organization_id=organization_id)

        rows: list[CashAccountRow] = []
        opening_total = ZERO
        closing_total = ZERO
        for row in trial.rows:
            if row.account_id not in cash_ids:
                continue
            opening = row.opening_debit - row.opening_credit
            closing = row.closing_debit - row.closing_credit
            opening_total += opening
            closing_total += closing
            rows.append(
                CashAccountRow(
                    account_id=row.account_id,
                    account_code=row.account_code,
                    account_name=row.account_name,
                    opening=quantize(opening, currency),
                    movement=quantize(row.period_debit - row.period_credit, currency),
                    closing=quantize(closing, currency),
                )
            )
        # A till that has existed all year but had no movement in the window
        # still belongs on the statement, at the balance it is holding.
        reported = {row.account_id for row in rows}
        for account_id in cash_ids - reported:
            account = accounts.get(account_id)
            if account is None:
                continue
            rows.append(
                CashAccountRow(
                    account_id=account_id,
                    account_code=account.code,
                    account_name=account.name,
                    opening=ZERO,
                    movement=ZERO,
                    closing=ZERO,
                )
            )
        rows.sort(key=lambda row: row.account_code)

        attributed = await self._attribute(
            organization_id=organization_id,
            cash_ids=cash_ids,
            from_date=from_date,
            to_date=to_date,
        )

        inflows: list[CashFlowLine] = []
        outflows: list[CashFlowLine] = []
        for account_id, amount in attributed.items():
            account = accounts.get(account_id)
            if account is None or amount == ZERO:
                continue
            line = CashFlowLine(
                account_id=account_id,
                account_code=account.code,
                account_name=account.name,
                element=account.element,
                amount=quantize(abs(amount), currency),
            )
            (inflows if amount > ZERO else outflows).append(line)

        inflows.sort(key=lambda line: line.account_code)
        outflows.sort(key=lambda line: line.account_code)

        total_in = quantize(sum((line.amount for line in inflows), ZERO), currency)
        total_out = quantize(sum((line.amount for line in outflows), ZERO), currency)
        net = quantize(total_in - total_out, currency)
        opening_total = quantize(opening_total, currency)
        closing_total = quantize(closing_total, currency)
        difference = quantize(opening_total + net - closing_total, currency)

        return CashFlow(
            from_date=from_date,
            to_date=to_date,
            currency=currency,
            opening_balance=opening_total,
            closing_balance=closing_total,
            total_in=total_in,
            total_out=total_out,
            net_movement=net,
            difference=difference,
            reconciles=difference == ZERO,
            cash_accounts=rows,
            inflows=inflows,
            outflows=outflows,
            no_cash_accounts_configured=False,
        )

    async def _cash_account_ids(self, *, organization_id: uuid.UUID) -> set[uuid.UUID]:
        """The ledger accounts the clinic's own tills and bank accounts use."""
        registers = await self._tills.list_registers(
            organization_id=organization_id, active_only=False
        )
        banks = await self._banking.list_accounts(
            organization_id=organization_id, include_inactive=True
        )
        return {register.account_id for register in registers} | {
            account.account_id for account in banks
        }

    async def _accounts(self, *, organization_id: uuid.UUID) -> dict[uuid.UUID, Any]:
        """Every account, including deactivated ones: a closed account can
        still carry a balance that a window being reported was moved by."""
        rows = await self._accounts_service.list_accounts(
            organization_id=organization_id, active_only=False
        )
        return {account.id: account for account in rows}

    async def _attribute(
        self,
        *,
        organization_id: uuid.UUID,
        cash_ids: set[uuid.UUID],
        from_date: date,
        to_date: date,
    ) -> dict[uuid.UUID, Decimal]:
        """Share each entry's cash movement across its own other side.

        Positive is cash in. The apportionment only ever divides an entry's
        cash figure by that same entry's non-cash figures, so it cannot
        invent a number or decide what a payment was *for*.
        """
        lines = await self._reports.entry_lines(
            organization_id=organization_id, from_date=from_date, to_date=to_date
        )

        by_entry: dict[uuid.UUID, list[EntryLine]] = {}
        for line in lines:
            by_entry.setdefault(line.entry_id, []).append(line)

        attributed: dict[uuid.UUID, Decimal] = {}
        for entry_lines in by_entry.values():
            cash_delta = sum(
                (line.debit - line.credit for line in entry_lines if line.account_id in cash_ids),
                ZERO,
            )
            if cash_delta == ZERO:
                continue
            others = [line for line in entry_lines if line.account_id not in cash_ids]
            weights = [abs(line.debit - line.credit) for line in others]
            total_weight = sum(weights, ZERO)
            if not others or total_weight == ZERO:
                # Cash moved between two cash accounts — a deposit from the
                # till to the bank. It changes no total and belongs nowhere
                # on a statement of what the money was for.
                continue
            running = ZERO
            for index, (line, weight) in enumerate(zip(others, weights, strict=True)):
                if index == len(others) - 1:
                    share = cash_delta - running
                else:
                    share = cash_delta * weight / total_weight
                    running += share
                attributed[line.account_id] = attributed.get(line.account_id, ZERO) + share
        return attributed
