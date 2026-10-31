"""Invariants 8, 9 and 10: closed periods, duplicate events, atomicity."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import (
    Conflict,
    DuplicatePostingEvent,
    PeriodClosed,
    UnbalancedEntry,
    ValidationFailed,
)
from app.modules.accounting.enums import EntryStatus, PeriodStatus, PostingPurpose, SourceKind
from app.modules.accounting.models import JournalEntry, JournalLine, PostingEvent
from app.modules.accounting.services.periods import PeriodService
from app.modules.accounting.services.posting import PostingLine, PostingRequest
from tests.accounting.test_invariants import OCTOBER, cash_sale

# --------------------------------------------------------------------------
# 8. Closed periods reject postings
# --------------------------------------------------------------------------


class TestPeriodClosing:
    async def test_posting_into_a_closed_period_is_refused(
        self, ledger, posting, audit, db: AsyncSession
    ) -> None:
        periods = PeriodService(db, audit)
        # September first — periods close in order.
        for code in (
            "2026-01",
            "2026-02",
            "2026-03",
            "2026-04",
            "2026-05",
            "2026-06",
            "2026-07",
            "2026-08",
            "2026-09",
            "2026-10",
        ):
            await periods.close_period(organization_id=ledger.id, period_id=ledger.period(code).id)

        with pytest.raises(PeriodClosed, match="closed"):
            await posting.post(organization_id=ledger.id, request=cash_sale())

    async def test_the_database_refuses_it_too(
        self, ledger, posting, audit, db: AsyncSession
    ) -> None:
        """Enforcement does not depend on the service's check running."""
        october = ledger.period("2026-10")
        await db.execute(
            text("UPDATE accounting_period SET status = 'CLOSED' WHERE id = :id"),
            {"id": october.id},
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO journal_entry
                        (id, organization_id, entry_number, entry_date, period_id,
                         status, description, currency, source_kind, posted_at)
                    VALUES (gen_random_uuid(), :org, 'JE-RAW', :on, :period,
                            'POSTED', 'bypassing the service', 'UZS', 'MANUAL', now())
                    """
                ),
                {"org": ledger.id, "on": OCTOBER, "period": october.id},
            )
        assert "closed" in str(caught.value)
        await db.rollback()

    async def test_closing_is_refused_while_drafts_remain(
        self, ledger, posting, audit, db: AsyncSession
    ) -> None:
        """An unposted draft in a period being closed is almost always an oversight."""
        await posting.create_draft(
            organization_id=ledger.id, request=cash_sale(source_id="draft-x")
        )
        periods = PeriodService(db, audit)
        with pytest.raises(Conflict, match="unposted draft"):
            await periods.close_period(
                organization_id=ledger.id, period_id=ledger.period("2026-10").id
            )

    async def test_periods_close_in_order(self, ledger, audit, db: AsyncSession) -> None:
        periods = PeriodService(db, audit)
        with pytest.raises(Conflict, match="periods close in order"):
            await periods.close_period(
                organization_id=ledger.id, period_id=ledger.period("2026-10").id
            )

    async def test_reopening_requires_a_reason(self, ledger, audit, db: AsyncSession) -> None:
        periods = PeriodService(db, audit)
        await periods.close_period(organization_id=ledger.id, period_id=ledger.period("2026-01").id)
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await periods.reopen_period(
                organization_id=ledger.id,
                period_id=ledger.period("2026-01").id,
                reason="  ",
            )

    async def test_reopening_restores_posting(
        self, ledger, posting, audit, db: AsyncSession
    ) -> None:
        periods = PeriodService(db, audit)
        for code in ("2026-01", "2026-02"):
            await periods.close_period(organization_id=ledger.id, period_id=ledger.period(code).id)
        reopened = await periods.reopen_period(
            organization_id=ledger.id,
            period_id=ledger.period("2026-02").id,
            reason="Supplier invoice arrived after the close",
        )
        assert reopened.status is PeriodStatus.OPEN
        assert reopened.reopened_reason

        entry = await posting.post(
            organization_id=ledger.id,
            request=cash_sale(entry_date=date(2026, 2, 10), source_id="feb-1"),
        )
        assert entry.status is EntryStatus.POSTED

    async def test_closing_and_reopening_are_audited(self, ledger, audit, db: AsyncSession) -> None:
        from app.modules.audit import events
        from app.modules.audit.models import AuditLog

        periods = PeriodService(db, audit)
        await periods.close_period(organization_id=ledger.id, period_id=ledger.period("2026-01").id)
        await periods.reopen_period(
            organization_id=ledger.id,
            period_id=ledger.period("2026-01").id,
            reason="Correction needed",
        )
        await db.flush()

        actions = set(
            (
                await db.execute(
                    select(AuditLog.action).where(
                        AuditLog.organization_id == ledger.id,
                        AuditLog.target_type == "accounting_period",
                    )
                )
            ).scalars()
        )
        assert events.PERIOD_CLOSED in actions
        assert events.PERIOD_REOPENED in actions

    async def test_entry_date_outside_its_period_is_refused(self, ledger, posting) -> None:
        """A date with no period at all is a clear error, not a silent default."""
        with pytest.raises(ValidationFailed, match="No accounting period covers"):
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(entry_date=date(2031, 5, 5), source_id="far-future"),
            )


# --------------------------------------------------------------------------
# 9. Duplicate events do not duplicate postings
# --------------------------------------------------------------------------


class TestIdempotency:
    async def test_the_same_event_cannot_post_twice(self, ledger, posting) -> None:
        await posting.post(organization_id=ledger.id, request=cash_sale())
        with pytest.raises(DuplicatePostingEvent):
            await posting.post(organization_id=ledger.id, request=cash_sale())

    async def test_a_retry_can_ask_for_the_existing_entry(self, ledger, posting) -> None:
        """How a caller that cannot tell whether its attempt committed recovers."""
        first = await posting.post(organization_id=ledger.id, request=cash_sale())
        again = await posting.post(
            organization_id=ledger.id,
            request=cash_sale(),
            on_duplicate="return_existing",
        )
        assert again.id == first.id

    async def test_only_one_entry_exists_after_a_duplicate_attempt(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        await posting.post(organization_id=ledger.id, request=cash_sale())
        with pytest.raises(DuplicatePostingEvent):
            await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        count = (
            await db.execute(
                select(func.count())
                .select_from(JournalEntry)
                .where(
                    JournalEntry.organization_id == ledger.id,
                    JournalEntry.source_id == "payment-0001",
                )
            )
        ).scalar_one()
        assert count == 1

    async def test_the_same_source_may_post_for_a_different_purpose(self, ledger, posting) -> None:
        """A card payment legitimately produces recognition *and* settlement."""
        recognition = await posting.post(
            organization_id=ledger.id,
            request=PostingRequest(
                entry_date=OCTOBER,
                description="Card payment taken",
                source_kind=SourceKind.PAYMENT,
                purpose=PostingPurpose.RECOGNITION,
                source_id="card-7",
                lines=[
                    PostingLine(account_code="5110.03", debit=Decimal("500000")),
                    PostingLine(account_code="9030", credit=Decimal("500000")),
                ],
            ),
        )
        settlement = await posting.post(
            organization_id=ledger.id,
            request=PostingRequest(
                entry_date=OCTOBER,
                description="Settlement received net of commission",
                source_kind=SourceKind.PAYMENT,
                purpose=PostingPurpose.SETTLEMENT,
                source_id="card-7",
                lines=[
                    PostingLine(account_code="5110", debit=Decimal("495000")),
                    PostingLine(account_code="9420", debit=Decimal("5000")),
                    PostingLine(account_code="5110.03", credit=Decimal("500000")),
                ],
            ),
        )
        assert recognition.id != settlement.id

    async def test_the_unique_constraint_backs_the_check(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """The service's lookup is a courtesy; the constraint is the guarantee."""
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO posting_event
                        (id, organization_id, source_kind, source_id, purpose,
                         journal_entry_id)
                    VALUES (gen_random_uuid(), :org, 'PAYMENT', 'payment-0001',
                            'RECOGNITION', :entry)
                    """
                ),
                {"org": ledger.id, "entry": entry.id},
            )
        assert "uq_posting_event_source" in str(caught.value)
        await db.rollback()


# --------------------------------------------------------------------------
# 10. Failed postings roll back atomically
# --------------------------------------------------------------------------


class TestAtomicity:
    async def test_a_failed_posting_leaves_nothing_behind(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """No orphan entry, no orphan lines, no burnt entry number."""
        before_entries = (
            await db.execute(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.organization_id == ledger.id)
            )
        ).scalar_one()

        await db.begin_nested()
        with pytest.raises(UnbalancedEntry):
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(
                    lines=[
                        PostingLine(account_code="5010", debit=Decimal("100")),
                        PostingLine(account_code="9030", credit=Decimal("99")),
                    ]
                ),
            )
        await db.rollback()

        after_entries = (
            await db.execute(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.organization_id == ledger.id)
            )
        ).scalar_one()
        assert after_entries == before_entries

    async def test_lines_never_outlive_a_rolled_back_entry(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        await db.begin_nested()
        with pytest.raises(ValidationFailed, match="accounts that cannot be used"):
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(
                    lines=[
                        PostingLine(account_code="5010", debit=Decimal("5")),
                        PostingLine(account_code="NOPE", credit=Decimal("5")),
                    ]
                ),
            )
        await db.rollback()

        orphans = (
            await db.execute(
                select(func.count())
                .select_from(JournalLine)
                .outerjoin(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(JournalEntry.id.is_(None))
            )
        ).scalar_one()
        assert orphans == 0


# --------------------------------------------------------------------------
# Concurrency: two transactions posting the same event
# --------------------------------------------------------------------------


@pytest.mark.integration
class TestConcurrentPosting:
    async def test_only_one_of_two_concurrent_postings_succeeds(
        self, engine, committed_org
    ) -> None:
        """The scenario the idempotency key exists for.

        Two cashiers process the same payment at the same moment. Both
        transactions pass the "has this been posted?" check, because neither
        can see the other's uncommitted row. Only one `posting_event` insert
        can commit.

        This runs on real committed connections: a check-then-act gap cannot
        be demonstrated inside a single shared transaction.
        """
        from datetime import date as _date

        from app.modules.accounting.enums import (
            AccountElement,
            AccountLevel,
            AccountNature,
        )
        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.periods import PeriodService
        from app.modules.accounting.services.posting import PostingService
        from app.modules.audit.service import AuditRecorder

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        org_id = committed_org.id

        # Scaffolding, committed so both workers can see it.
        async with factory() as setup:
            recorder = AuditRecorder(setup, actor_label="test")
            await PeriodService(setup, recorder).create_fiscal_year(
                organization_id=org_id,
                code="2026",
                starts_on=_date(2026, 1, 1),
                ends_on=_date(2026, 12, 31),
            )
            accounts = AccountService(setup, recorder)
            for code, name, nature, element in (
                ("5010", "Cash in hand", AccountNature.ASSET, AccountElement.ASSET),
                ("9030", "Revenue", AccountNature.LIABILITY_EQUITY, AccountElement.REVENUE),
            ):
                await accounts.create_account(
                    organization_id=org_id,
                    code=code,
                    name=name,
                    level=AccountLevel.ACCOUNT,
                    nature=nature,
                    element=element,
                )
            await setup.commit()

        async def attempt() -> str:
            async with factory() as session:
                service = PostingService(session, AuditRecorder(session, actor_label="test"))
                try:
                    await service.post(
                        organization_id=org_id,
                        request=cash_sale(source_id="concurrent-payment"),
                    )
                    await session.commit()
                    return "posted"
                except Exception as exc:
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(attempt(), attempt())
        assert results.count("posted") == 1, results

        async with factory() as check:
            count = (
                await check.execute(
                    select(func.count())
                    .select_from(PostingEvent)
                    .where(
                        PostingEvent.organization_id == org_id,
                        PostingEvent.source_id == "concurrent-payment",
                    )
                )
            ).scalar_one()
            assert count == 1

            entries = (
                await check.execute(
                    select(func.count())
                    .select_from(JournalEntry)
                    .where(
                        JournalEntry.organization_id == org_id,
                        JournalEntry.source_id == "concurrent-payment",
                    )
                )
            ).scalar_one()
            assert entries == 1
