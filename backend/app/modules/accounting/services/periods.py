"""Fiscal years and accounting periods.

Closing a period is what makes a reported figure stable. Reopening one is
possible — an accountant sometimes must — but it requires a distinct
permission and a reason, and both the close and the reopen are audited.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.enums import (
    EntryStatus,
    FiscalYearStatus,
    PeriodStatus,
)
from app.modules.accounting.models import (
    AccountingPeriod,
    FiscalYear,
    JournalEntry,
)
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder


class PeriodService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ----------------------------------------------------------- fiscal year

    async def create_fiscal_year(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        starts_on: date,
        ends_on: date,
        create_monthly_periods: bool = True,
    ) -> FiscalYear:
        """Create a year and, by default, its twelve monthly periods."""
        if ends_on <= starts_on:
            raise ValidationFailed("A fiscal year must end after it starts")

        existing = (
            await self._session.execute(
                select(FiscalYear).where(
                    FiscalYear.organization_id == organization_id,
                    FiscalYear.code == code,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"Fiscal year {code!r} already exists")

        # Overlapping years would make the period lookup for a date ambiguous.
        overlap = (
            await self._session.execute(
                select(FiscalYear).where(
                    FiscalYear.organization_id == organization_id,
                    FiscalYear.starts_on <= ends_on,
                    FiscalYear.ends_on >= starts_on,
                )
            )
        ).scalar_one_or_none()
        if overlap is not None:
            raise Conflict(
                f"This range overlaps fiscal year {overlap.code}",
                details={"overlaps": overlap.code},
            )

        year = FiscalYear(
            organization_id=organization_id,
            code=code,
            starts_on=starts_on,
            ends_on=ends_on,
            status=FiscalYearStatus.OPEN,
        )
        self._session.add(year)
        await self._session.flush()

        if create_monthly_periods:
            await self._create_monthly_periods(year)

        self._audit.record(
            "accounting.fiscal_year.created",
            organization_id=organization_id,
            target_type="fiscal_year",
            target_id=year.id,
            target_label=code,
            new_values={"starts_on": starts_on, "ends_on": ends_on},
        )
        return year

    async def _create_monthly_periods(self, year: FiscalYear) -> list[AccountingPeriod]:
        periods: list[AccountingPeriod] = []
        cursor = year.starts_on
        while cursor <= year.ends_on:
            last_day = calendar.monthrange(cursor.year, cursor.month)[1]
            period_end = min(date(cursor.year, cursor.month, last_day), year.ends_on)
            period = AccountingPeriod(
                organization_id=year.organization_id,
                fiscal_year_id=year.id,
                code=f"{cursor.year}-{cursor.month:02d}",
                starts_on=cursor,
                ends_on=period_end,
                status=PeriodStatus.OPEN,
            )
            self._session.add(period)
            periods.append(period)
            cursor = (
                date(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else date(cursor.year, cursor.month + 1, 1)
            )
        await self._session.flush()
        return periods

    async def list_fiscal_years(self, organization_id: uuid.UUID) -> list[FiscalYear]:
        stmt = (
            select(FiscalYear)
            .where(FiscalYear.organization_id == organization_id)
            .order_by(FiscalYear.starts_on.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # --------------------------------------------------------------- periods

    async def list_periods(
        self, organization_id: uuid.UUID, fiscal_year_id: uuid.UUID | None = None
    ) -> list[AccountingPeriod]:
        stmt = select(AccountingPeriod).where(AccountingPeriod.organization_id == organization_id)
        if fiscal_year_id is not None:
            stmt = stmt.where(AccountingPeriod.fiscal_year_id == fiscal_year_id)
        return list(
            (await self._session.execute(stmt.order_by(AccountingPeriod.starts_on))).scalars().all()
        )

    async def get_period(
        self, *, organization_id: uuid.UUID, period_id: uuid.UUID
    ) -> AccountingPeriod:
        stmt = select(AccountingPeriod).where(
            AccountingPeriod.organization_id == organization_id,
            AccountingPeriod.id == period_id,
        )
        period = (await self._session.execute(stmt)).scalar_one_or_none()
        if period is None:
            raise NotFound("Accounting period not found")
        return period

    async def close_period(
        self,
        *,
        organization_id: uuid.UUID,
        period_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> AccountingPeriod:
        """Close a period, refusing while drafts remain.

        An unposted draft in a period being closed is almost always an
        oversight — the work was prepared and never posted. Reporting the
        period as final while that sits there would be misleading, so the
        close is refused and the drafts are named.
        """
        period = await self.get_period(organization_id=organization_id, period_id=period_id)
        if period.status is PeriodStatus.CLOSED:
            raise Conflict(f"Period {period.code} is already closed")

        draft_count = (
            await self._session.execute(
                select(func.count())
                .select_from(JournalEntry)
                .where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntry.period_id == period_id,
                    JournalEntry.status == EntryStatus.DRAFT,
                )
            )
        ).scalar_one()
        if draft_count:
            raise Conflict(
                f"Period {period.code} still has {draft_count} unposted draft "
                f"{'entry' if draft_count == 1 else 'entries'}. "
                "Post or delete them before closing.",
                details={"draft_count": int(draft_count), "period": period.code},
            )

        # An earlier open period would let a posting slip in behind a closed
        # one, which breaks the assumption that closing freezes a reported run.
        earlier_open = (
            await self._session.execute(
                select(AccountingPeriod)
                .where(
                    AccountingPeriod.organization_id == organization_id,
                    AccountingPeriod.status == PeriodStatus.OPEN,
                    AccountingPeriod.ends_on < period.starts_on,
                )
                .order_by(AccountingPeriod.starts_on)
                .limit(1)
            )
        ).scalar_one_or_none()
        if earlier_open is not None:
            raise Conflict(
                f"Close {earlier_open.code} first — periods close in order",
                details={"earliest_open": earlier_open.code},
            )

        period.status = PeriodStatus.CLOSED
        period.closed_at = datetime.now(UTC)
        period.closed_by_user_id = actor_user_id
        await self._session.flush()

        self._audit.record(
            events.PERIOD_CLOSED,
            organization_id=organization_id,
            target_type="accounting_period",
            target_id=period.id,
            target_label=period.code,
            old_values={"status": "OPEN"},
            new_values={"status": "CLOSED"},
        )
        return period

    async def reopen_period(
        self,
        *,
        organization_id: uuid.UUID,
        period_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> AccountingPeriod:
        """Reopen a closed period. Requires a reason, and is audited.

        Deliberately possible: refusing outright would push corrections into
        the wrong period, which is worse. The control is that it is explicit,
        justified and recorded, not that it is impossible.
        """
        if not reason or not reason.strip():
            raise ValidationFailed(
                "Reopening a period requires a reason", details={"field": "reason"}
            )
        period = await self.get_period(organization_id=organization_id, period_id=period_id)
        if period.status is PeriodStatus.OPEN:
            raise Conflict(f"Period {period.code} is already open")

        year = await self._session.get(FiscalYear, period.fiscal_year_id)
        if year is not None and year.status is FiscalYearStatus.CLOSED:
            raise Conflict(
                f"Fiscal year {year.code} is closed. Reopen the year first.",
                details={"fiscal_year": year.code},
            )

        period.status = PeriodStatus.OPEN
        period.closed_at = None
        period.closed_by_user_id = None
        period.reopened_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            events.PERIOD_REOPENED,
            organization_id=organization_id,
            target_type="accounting_period",
            target_id=period.id,
            target_label=period.code,
            reason=reason.strip(),
            old_values={"status": "CLOSED"},
            new_values={"status": "OPEN"},
        )
        return period
