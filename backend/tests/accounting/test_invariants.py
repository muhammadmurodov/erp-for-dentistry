"""The ten accounting invariants, one section each.

Each invariant is tested at the service level *and*, where the database
enforces it, again with the service bypassed — because the point of the
database enforcement is to hold when application code does not.

Numbering matches docs/ACCOUNTING_DESIGN.md.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    Conflict,
    UnbalancedEntry,
    ValidationFailed,
)
from app.modules.accounting.enums import EntryStatus, PostingPurpose, SourceKind
from app.modules.accounting.services.posting import PostingLine, PostingRequest

OCTOBER = date(2026, 10, 15)


def cash_sale(amount: str = "300000", **overrides) -> PostingRequest:
    """A patient pays cash for a completed treatment."""
    defaults: dict = {
        "entry_date": OCTOBER,
        "description": "Cash payment for dental treatment",
        "source_kind": SourceKind.PAYMENT,
        "purpose": PostingPurpose.RECOGNITION,
        "source_id": "payment-0001",
        "lines": [
            PostingLine(account_code="5010", debit=Decimal(amount)),
            PostingLine(account_code="9030", credit=Decimal(amount)),
        ],
    }
    defaults.update(overrides)
    return PostingRequest(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 1 & 2. A posted entry balances
# --------------------------------------------------------------------------


class TestBalanceInvariant:
    async def test_balanced_entry_posts(self, ledger, posting) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        assert entry.status is EntryStatus.POSTED
        assert entry.entry_number.startswith("JE-")

        lines = await posting.get_lines(organization_id=ledger.id, entry_id=entry.id)
        assert len(lines) == 2
        assert sum(line.debit for line in lines) == sum(line.credit for line in lines)

    async def test_unbalanced_entry_is_refused(self, ledger, posting) -> None:
        with pytest.raises(UnbalancedEntry) as caught:
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(
                    lines=[
                        PostingLine(account_code="5010", debit=Decimal("300000")),
                        PostingLine(account_code="9030", credit=Decimal("250000")),
                    ]
                ),
            )
        assert caught.value.details["difference"] == "50000.00"

    async def test_multi_line_entry_balances(self, ledger, posting) -> None:
        """A card payment settling net of commission needs three lines."""
        entry = await posting.post(
            organization_id=ledger.id,
            request=PostingRequest(
                entry_date=OCTOBER,
                description="Card settlement received net of bank commission",
                source_kind=SourceKind.CARD_SETTLEMENT,
                purpose=PostingPurpose.SETTLEMENT,
                source_id="settlement-0001",
                lines=[
                    PostingLine(account_code="5110", debit=Decimal("495000")),
                    PostingLine(account_code="9420", debit=Decimal("5000")),
                    PostingLine(account_code="5110.03", credit=Decimal("500000")),
                ],
            ),
        )
        lines = await posting.get_lines(organization_id=ledger.id, entry_id=entry.id)
        assert len(lines) == 3
        assert sum(line.debit for line in lines) == Decimal("500000.00")
        assert sum(line.credit for line in lines) == Decimal("500000.00")

    async def test_single_line_entry_is_refused(self, ledger, posting) -> None:
        with pytest.raises(UnbalancedEntry, match="at least 2 lines"):
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(lines=[PostingLine(account_code="5010", debit=Decimal("1"))]),
            )

    async def test_zero_total_entry_is_refused(self, ledger, posting) -> None:
        with pytest.raises(ValidationFailed) as caught:
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(
                    lines=[
                        PostingLine(account_code="5010", debit=Decimal("0")),
                        PostingLine(account_code="9030", credit=Decimal("0")),
                    ]
                ),
            )
        problems = caught.value.details["problems"]
        assert all("must carry an amount" in p for p in problems)

    async def test_negative_amount_is_refused(self, ledger, posting) -> None:
        with pytest.raises(ValidationFailed) as caught:
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(
                    lines=[
                        PostingLine(account_code="5010", debit=Decimal("-100")),
                        PostingLine(account_code="9030", credit=Decimal("-100")),
                    ]
                ),
            )
        assert any("must not be negative" in p for p in caught.value.details["problems"])

    async def test_line_cannot_be_both_debit_and_credit(self, ledger, posting) -> None:
        with pytest.raises(ValidationFailed) as caught:
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(
                    lines=[
                        PostingLine(
                            account_code="5010",
                            debit=Decimal("100"),
                            credit=Decimal("100"),
                        ),
                        PostingLine(account_code="9030", credit=Decimal("100")),
                    ]
                ),
            )
        assert any("debit or a credit, not both" in p for p in caught.value.details["problems"])

    async def test_database_refuses_an_unbalanced_entry_without_the_service(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """The guarantee: enforcement does not depend on the service running.

        A balanced entry is posted, then a credit line is inserted by raw SQL
        to unbalance it. The deferred constraint trigger catches it.
        """
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO journal_line
                        (id, organization_id, journal_entry_id, line_number,
                         account_id, debit, credit, entry_date, period_id, entry_status)
                    VALUES (gen_random_uuid(), :org, :entry, 99, :account,
                            0, 1, :on, :period, 'POSTED')
                    """
                ),
                {
                    "org": ledger.id,
                    "entry": entry.id,
                    "account": ledger.account("9030").id,
                    "on": OCTOBER,
                    "period": entry.period_id,
                },
            )
            # Forces the deferred constraint trigger to run now.
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "does not balance" in str(caught.value)
        await db.rollback()


# --------------------------------------------------------------------------
# 3. Lines belong to their entry's organization
# --------------------------------------------------------------------------


class TestOrganizationScoping:
    async def test_cross_organization_line_is_unrepresentable(
        self, ledger, posting, org_b, db: AsyncSession
    ) -> None:
        """The composite foreign key refuses it however the insert is phrased."""
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        await db.begin_nested()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    """
                    INSERT INTO journal_line
                        (id, organization_id, journal_entry_id, line_number,
                         account_id, debit, credit, entry_date, period_id, entry_status)
                    VALUES (gen_random_uuid(), :other_org, :entry, 98, :account,
                            1, 0, :on, :period, 'POSTED')
                    """
                ),
                {
                    "other_org": org_b.id,
                    "entry": entry.id,
                    "account": ledger.account("5010").id,
                    "on": OCTOBER,
                    "period": entry.period_id,
                },
            )
            await db.flush()
        await db.rollback()

    async def test_another_organizations_entry_is_not_found(
        self, ledger, posting, org_b, audit, db: AsyncSession
    ) -> None:
        from app.core.errors import NotFound
        from app.modules.accounting.services.posting import PostingService

        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        other = PostingService(db, audit)
        with pytest.raises(NotFound):
            await other.get_entry(organization_id=org_b.id, entry_id=entry.id)


# --------------------------------------------------------------------------
# 4 & 5. Posted entries are immutable; corrections are reversals
# --------------------------------------------------------------------------


class TestImmutability:
    async def test_posted_entry_cannot_be_edited(self, ledger, posting, db: AsyncSession) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("UPDATE journal_entry SET description = 'tampered' WHERE id = :id"),
                {"id": entry.id},
            )
        assert "cannot be edited" in str(caught.value)
        await db.rollback()

    async def test_posted_entry_cannot_be_deleted(self, ledger, posting, db: AsyncSession) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(text("DELETE FROM journal_entry WHERE id = :id"), {"id": entry.id})
        assert "cannot be deleted" in str(caught.value)
        await db.rollback()

    async def test_posted_line_cannot_be_edited(self, ledger, posting, db: AsyncSession) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    "UPDATE journal_line SET debit = 999999 "
                    "WHERE journal_entry_id = :id AND line_number = 1"
                ),
                {"id": entry.id},
            )
        assert "lines cannot be edited" in str(caught.value)
        await db.rollback()

    async def test_a_draft_can_be_edited_and_deleted(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """Drafts are working documents; only posting makes an entry final."""
        draft = await posting.create_draft(
            organization_id=ledger.id,
            request=cash_sale(source_id="draft-1"),
        )
        await db.flush()
        await db.execute(
            text("UPDATE journal_entry SET description = 'revised' WHERE id = :id"),
            {"id": draft.id},
        )
        await db.execute(text("DELETE FROM journal_entry WHERE id = :id"), {"id": draft.id})
        await db.flush()


class TestReversal:
    async def test_reversal_mirrors_the_original(self, ledger, posting) -> None:
        original = await posting.post(organization_id=ledger.id, request=cash_sale())
        reversal = await posting.reverse(
            organization_id=ledger.id,
            entry_id=original.id,
            reason="Treatment cancelled; payment refunded",
            reversal_date=OCTOBER,
        )

        original_lines = await posting.get_lines(organization_id=ledger.id, entry_id=original.id)
        reversal_lines = await posting.get_lines(organization_id=ledger.id, entry_id=reversal.id)
        assert len(reversal_lines) == len(original_lines)
        for before, after in zip(original_lines, reversal_lines, strict=True):
            assert after.account_id == before.account_id
            assert after.debit == before.credit
            assert after.credit == before.debit

    async def test_reversal_nets_the_account_to_zero(self, ledger, posting) -> None:
        """The point of a reversal: no net effect on any balance."""
        from app.modules.accounting.services.accounts import AccountService

        original = await posting.post(organization_id=ledger.id, request=cash_sale())
        await posting.reverse(
            organization_id=ledger.id,
            entry_id=original.id,
            reason="Posted in error",
            reversal_date=OCTOBER,
        )
        accounts = AccountService(posting._session, posting._audit)
        for code in ("5010", "9030"):
            balance = await accounts.balance(
                organization_id=ledger.id, account_id=ledger.account(code).id
            )
            assert balance == Decimal("0"), code

    async def test_both_entries_are_linked(self, ledger, posting) -> None:
        original = await posting.post(organization_id=ledger.id, request=cash_sale())
        reversal = await posting.reverse(
            organization_id=ledger.id,
            entry_id=original.id,
            reason="Posted in error",
            reversal_date=OCTOBER,
        )
        assert reversal.reversal_of_entry_id == original.id
        assert original.reversed_by_entry_id == reversal.id

    async def test_reversal_requires_a_reason(self, ledger, posting) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await posting.reverse(organization_id=ledger.id, entry_id=entry.id, reason="   ")

    async def test_an_entry_cannot_be_reversed_twice(self, ledger, posting) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await posting.reverse(
            organization_id=ledger.id,
            entry_id=entry.id,
            reason="first",
            reversal_date=OCTOBER,
        )
        with pytest.raises(Conflict, match="already been reversed"):
            await posting.reverse(
                organization_id=ledger.id,
                entry_id=entry.id,
                reason="second",
                reversal_date=OCTOBER,
            )

    async def test_a_draft_cannot_be_reversed(self, ledger, posting) -> None:
        draft = await posting.create_draft(
            organization_id=ledger.id, request=cash_sale(source_id="draft-2")
        )
        with pytest.raises(Conflict, match="Only a posted entry"):
            await posting.reverse(organization_id=ledger.id, entry_id=draft.id, reason="nope")

    async def test_the_original_stays_visible_after_reversal(self, ledger, posting) -> None:
        """A reversal adds history; it does not hide it."""
        original = await posting.post(organization_id=ledger.id, request=cash_sale())
        await posting.reverse(
            organization_id=ledger.id,
            entry_id=original.id,
            reason="Posted in error",
            reversal_date=OCTOBER,
        )
        still_there = await posting.get_entry(organization_id=ledger.id, entry_id=original.id)
        assert still_there.status is EntryStatus.POSTED
        assert still_there.description == "Cash payment for dental treatment"


# --------------------------------------------------------------------------
# 6 & 7. Traceability and unique numbering
# --------------------------------------------------------------------------


class TestTraceability:
    async def test_entry_records_its_originating_event(self, ledger, posting) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        assert entry.source_kind is SourceKind.PAYMENT
        assert entry.source_id == "payment-0001"

        found = await posting.find_by_source(
            organization_id=ledger.id,
            source_kind=SourceKind.PAYMENT,
            source_id="payment-0001",
        )
        assert [e.id for e in found] == [entry.id]

    async def test_entry_numbers_are_unique_and_sequential(self, ledger, posting) -> None:
        numbers = []
        for index in range(5):
            entry = await posting.post(
                organization_id=ledger.id,
                request=cash_sale(source_id=f"payment-{index:04d}"),
            )
            numbers.append(entry.entry_number)
        assert len(set(numbers)) == 5
        assert numbers == sorted(numbers)

    async def test_numbering_is_per_organization(
        self, ledger, posting, org_b, audit, db: AsyncSession
    ) -> None:
        """Two tenants both start at 1; neither can infer the other's volume."""
        from datetime import date as _date

        from app.modules.accounting.enums import AccountElement, AccountLevel, AccountNature
        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.periods import PeriodService
        from app.modules.accounting.services.posting import PostingService

        first = await posting.post(organization_id=ledger.id, request=cash_sale())

        periods = PeriodService(db, audit)
        year = await periods.create_fiscal_year(
            organization_id=org_b.id,
            code="2026",
            starts_on=_date(2026, 1, 1),
            ends_on=_date(2026, 12, 31),
        )
        del year
        accounts = AccountService(db, audit)
        for code, name, element in (
            ("5010", "Cash in hand", AccountElement.ASSET),
            ("9030", "Revenue", AccountElement.REVENUE),
        ):
            await accounts.create_account(
                organization_id=org_b.id,
                code=code,
                name=name,
                level=AccountLevel.ACCOUNT,
                nature=AccountNature.ASSET,
                element=element,
            )
        other = PostingService(db, audit)
        second = await other.post(organization_id=org_b.id, request=cash_sale())
        assert first.entry_number == second.entry_number == "JE-000001"
