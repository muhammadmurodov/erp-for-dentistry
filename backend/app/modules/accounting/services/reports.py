"""Financial reports.

Every figure is computed from posted journal lines at the moment it is asked
for. No report total is ever stored: a stored total is a second version of the
facts, and the two diverge. That is the rule the legacy workbook broke by
keeping pivot summaries beside the ledger.

Draft entries are excluded everywhere. A draft is work in progress and must
not move a reported balance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, quantize
from app.modules.accounting.enums import (
    DEBIT_NORMAL_NATURES,
    AccountElement,
    AccountNature,
    EntryStatus,
)
from app.modules.accounting.models import Account, JournalEntry, JournalLine


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    account_id: uuid.UUID
    account_code: str
    account_name: str
    nature: AccountNature | None
    element: AccountElement | None
    opening_debit: Decimal
    opening_credit: Decimal
    period_debit: Decimal
    period_credit: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


@dataclass(frozen=True, slots=True)
class TrialBalance:
    from_date: date
    to_date: date
    currency: str
    rows: list[TrialBalanceRow]
    total_opening_debit: Decimal
    total_opening_credit: Decimal
    total_period_debit: Decimal
    total_period_credit: Decimal
    total_closing_debit: Decimal
    total_closing_credit: Decimal

    @property
    def is_balanced(self) -> bool:
        """True when every column pair agrees.

        If this is ever False the ledger has been corrupted by something that
        bypassed the posting service, because the database's deferred balance
        trigger makes an unbalanced entry impossible to commit.
        """
        return (
            self.total_opening_debit == self.total_opening_credit
            and self.total_period_debit == self.total_period_credit
            and self.total_closing_debit == self.total_closing_credit
        )


@dataclass(frozen=True, slots=True)
class LedgerLine:
    entry_id: uuid.UUID
    entry_number: str
    entry_date: date
    description: str
    line_description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    is_reversal: bool
    reversed: bool


@dataclass(frozen=True, slots=True)
class AccountStatement:
    account_id: uuid.UUID
    account_code: str
    account_name: str
    nature: AccountNature | None
    from_date: date
    to_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    lines: list[LedgerLine]


def _signed(nature: AccountNature | None, debit: Decimal, credit: Decimal) -> Decimal:
    """Balance in the account's normal direction.

    A debit-normal account (an asset) reports debits positive; everything else
    reports credits positive. Without this a liability of 500 would display as
    -500, which is correct arithmetic and the wrong thing to show an
    accountant.
    """
    if nature is None or nature in DEBIT_NORMAL_NATURES:
        return debit - credit
    return credit - debit


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _posted(self, organization_id: uuid.UUID) -> Select:
        return select(JournalLine).where(
            JournalLine.organization_id == organization_id,
            JournalLine.entry_status == EntryStatus.POSTED,
        )

    # --------------------------------------------------------- trial balance

    async def trial_balance(
        self,
        *,
        organization_id: uuid.UUID,
        from_date: date,
        to_date: date,
        currency: str = "UZS",
        include_zero: bool = False,
    ) -> TrialBalance:
        """Opening, movement and closing per account.

        Opening is everything posted strictly before `from_date`, movement is
        the window, closing is their sum. Computed in two aggregate queries
        rather than per account, so the cost does not grow with the chart.
        """
        opening = await self._totals_by_account(organization_id=organization_id, before=from_date)
        movement = await self._totals_by_account(
            organization_id=organization_id, from_date=from_date, to_date=to_date
        )

        touched = set(opening) | set(movement)
        if not touched:
            return TrialBalance(
                from_date=from_date,
                to_date=to_date,
                currency=currency,
                rows=[],
                total_opening_debit=ZERO,
                total_opening_credit=ZERO,
                total_period_debit=ZERO,
                total_period_credit=ZERO,
                total_closing_debit=ZERO,
                total_closing_credit=ZERO,
            )

        accounts = {
            a.id: a
            for a in (
                await self._session.execute(
                    select(Account)
                    .where(
                        Account.organization_id == organization_id,
                        Account.id.in_(touched),
                    )
                    .order_by(Account.code)
                )
            ).scalars()
        }

        rows: list[TrialBalanceRow] = []
        for account in sorted(accounts.values(), key=lambda a: a.code):
            open_d, open_c = opening.get(account.id, (ZERO, ZERO))
            move_d, move_c = movement.get(account.id, (ZERO, ZERO))

            # A trial balance shows each account on one side only: the net of
            # its own debits and credits, not both gross totals.
            open_net = open_d - open_c
            close_net = open_net + move_d - move_c

            row = TrialBalanceRow(
                account_id=account.id,
                account_code=account.code,
                account_name=account.name,
                nature=account.nature,
                element=account.element,
                opening_debit=quantize(open_net, currency) if open_net > ZERO else ZERO,
                opening_credit=quantize(-open_net, currency) if open_net < ZERO else ZERO,
                period_debit=quantize(move_d, currency),
                period_credit=quantize(move_c, currency),
                closing_debit=quantize(close_net, currency) if close_net > ZERO else ZERO,
                closing_credit=quantize(-close_net, currency) if close_net < ZERO else ZERO,
            )
            if not include_zero and all(
                v == ZERO
                for v in (
                    row.opening_debit,
                    row.opening_credit,
                    row.period_debit,
                    row.period_credit,
                    row.closing_debit,
                    row.closing_credit,
                )
            ):
                continue
            rows.append(row)

        def total(attr: str) -> Decimal:
            return quantize(sum((getattr(r, attr) for r in rows), ZERO), currency)

        return TrialBalance(
            from_date=from_date,
            to_date=to_date,
            currency=currency,
            rows=rows,
            total_opening_debit=total("opening_debit"),
            total_opening_credit=total("opening_credit"),
            total_period_debit=total("period_debit"),
            total_period_credit=total("period_credit"),
            total_closing_debit=total("closing_debit"),
            total_closing_credit=total("closing_credit"),
        )

    async def _totals_by_account(
        self,
        *,
        organization_id: uuid.UUID,
        from_date: date | None = None,
        to_date: date | None = None,
        before: date | None = None,
    ) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
        conditions = [
            JournalLine.organization_id == organization_id,
            JournalLine.entry_status == EntryStatus.POSTED,
        ]
        if before is not None:
            conditions.append(JournalLine.entry_date < before)
        if from_date is not None:
            conditions.append(JournalLine.entry_date >= from_date)
        if to_date is not None:
            conditions.append(JournalLine.entry_date <= to_date)

        stmt = (
            select(
                JournalLine.account_id,
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .where(and_(*conditions))
            .group_by(JournalLine.account_id)
        )
        return {
            account_id: (Decimal(debit), Decimal(credit))
            for account_id, debit, credit in (await self._session.execute(stmt)).all()
        }

    # ------------------------------------------------------ account statement

    async def account_statement(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        from_date: date,
        to_date: date,
        currency: str = "UZS",
    ) -> AccountStatement:
        """One account's movements with a running balance."""
        account = (
            await self._session.execute(
                select(Account).where(
                    Account.organization_id == organization_id,
                    Account.id == account_id,
                )
            )
        ).scalar_one()

        before = await self._totals_by_account(organization_id=organization_id, before=from_date)
        open_d, open_c = before.get(account_id, (ZERO, ZERO))
        opening = _signed(account.nature, open_d, open_c)

        stmt = (
            select(JournalLine, JournalEntry)
            .join(
                JournalEntry,
                and_(
                    JournalEntry.id == JournalLine.journal_entry_id,
                    JournalEntry.organization_id == JournalLine.organization_id,
                ),
            )
            .where(
                JournalLine.organization_id == organization_id,
                JournalLine.account_id == account_id,
                JournalLine.entry_status == EntryStatus.POSTED,
                JournalLine.entry_date >= from_date,
                JournalLine.entry_date <= to_date,
            )
            .order_by(JournalLine.entry_date, JournalEntry.entry_number, JournalLine.line_number)
        )

        running = opening
        lines: list[LedgerLine] = []
        total_debit = ZERO
        total_credit = ZERO
        for line, entry in (await self._session.execute(stmt)).all():
            running += _signed(account.nature, line.debit, line.credit)
            total_debit += line.debit
            total_credit += line.credit
            lines.append(
                LedgerLine(
                    entry_id=entry.id,
                    entry_number=entry.entry_number,
                    entry_date=line.entry_date,
                    description=entry.description,
                    line_description=line.description,
                    debit=quantize(line.debit, currency),
                    credit=quantize(line.credit, currency),
                    running_balance=quantize(running, currency),
                    is_reversal=entry.reversal_of_entry_id is not None,
                    reversed=entry.reversed_by_entry_id is not None,
                )
            )

        return AccountStatement(
            account_id=account.id,
            account_code=account.code,
            account_name=account.name,
            nature=account.nature,
            from_date=from_date,
            to_date=to_date,
            opening_balance=quantize(opening, currency),
            closing_balance=quantize(running, currency),
            total_debit=quantize(total_debit, currency),
            total_credit=quantize(total_credit, currency),
            lines=lines,
        )

    # -------------------------------------------------------- general ledger

    async def general_ledger(
        self,
        *,
        organization_id: uuid.UUID,
        from_date: date,
        to_date: date,
        currency: str = "UZS",
    ) -> list[AccountStatement]:
        """A statement for every account that moved in the window."""
        moved = await self._totals_by_account(
            organization_id=organization_id, from_date=from_date, to_date=to_date
        )
        if not moved:
            return []
        accounts = (
            (
                await self._session.execute(
                    select(Account)
                    .where(
                        Account.organization_id == organization_id,
                        Account.id.in_(moved.keys()),
                    )
                    .order_by(Account.code)
                )
            )
            .scalars()
            .all()
        )
        return [
            await self.account_statement(
                organization_id=organization_id,
                account_id=account.id,
                from_date=from_date,
                to_date=to_date,
                currency=currency,
            )
            for account in accounts
        ]
