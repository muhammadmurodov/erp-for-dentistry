"""Trial balance, account statements, and the policy-versioning guarantee."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.modules.accounting.enums import PolicyStatus, PostingPurpose, SourceKind
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import PostingLine, PostingRequest
from app.modules.accounting.services.reports import ReportService
from tests.accounting.test_invariants import OCTOBER, cash_sale

YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


class TestTrialBalance:
    async def test_an_empty_ledger_balances(self, ledger, db: AsyncSession) -> None:
        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.rows == []
        assert report.is_balanced

    async def test_totals_agree_after_posting(self, ledger, posting, db: AsyncSession) -> None:
        await posting.post(organization_id=ledger.id, request=cash_sale("300000"))
        await posting.post(
            organization_id=ledger.id,
            request=cash_sale("150000", source_id="payment-0002"),
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.is_balanced
        assert report.total_period_debit == Decimal("450000.00")
        assert report.total_period_credit == Decimal("450000.00")
        assert report.total_closing_debit == report.total_closing_credit

    async def test_each_account_appears_once_on_one_side(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """A trial balance nets an account; it does not show both gross totals."""
        await posting.post(organization_id=ledger.id, request=cash_sale("300000"))
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        cash = next(r for r in report.rows if r.account_code == "5010")
        revenue = next(r for r in report.rows if r.account_code == "9030")

        assert cash.closing_debit == Decimal("300000.00")
        assert cash.closing_credit == Decimal("0")
        assert revenue.closing_credit == Decimal("300000.00")
        assert revenue.closing_debit == Decimal("0")

    async def test_drafts_do_not_move_a_balance(self, ledger, posting, db: AsyncSession) -> None:
        """Work in progress must never appear in a reported figure."""
        await posting.create_draft(
            organization_id=ledger.id, request=cash_sale(source_id="draft-only")
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.rows == []
        assert report.total_period_debit == Decimal("0")

    async def test_a_reversal_returns_the_balance_to_zero(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await posting.reverse(
            organization_id=ledger.id,
            entry_id=entry.id,
            reason="Posted in error",
            reversal_date=OCTOBER,
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert report.is_balanced
        # Gross movement doubles; the net closing position is nil.
        assert report.total_period_debit == Decimal("600000.00")
        assert report.total_closing_debit == Decimal("0")
        assert report.total_closing_credit == Decimal("0")

    async def test_opening_balance_carries_from_an_earlier_period(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        await posting.post(
            organization_id=ledger.id,
            request=cash_sale("100000", entry_date=date(2026, 3, 10), source_id="mar"),
        )
        await posting.post(
            organization_id=ledger.id,
            request=cash_sale("50000", entry_date=date(2026, 10, 10), source_id="oct"),
        )
        await db.flush()

        october = await ReportService(db).trial_balance(
            organization_id=ledger.id,
            from_date=date(2026, 10, 1),
            to_date=date(2026, 10, 31),
        )
        cash = next(r for r in october.rows if r.account_code == "5010")
        assert cash.opening_debit == Decimal("100000.00")
        assert cash.period_debit == Decimal("50000.00")
        assert cash.closing_debit == Decimal("150000.00")
        assert october.is_balanced

    async def test_the_card_payment_scenario_recognises_revenue_once(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """The worked example from ACCOUNTING_DESIGN.md.

        500 000 paid by card, 5 000 commission, 495 000 settled. Revenue must
        be 500 000 — not 495 000, and not 1 000 000 from counting both stages.
        """
        await posting.post(
            organization_id=ledger.id,
            request=PostingRequest(
                entry_date=OCTOBER,
                description="Card payment taken",
                source_kind=SourceKind.PAYMENT,
                purpose=PostingPurpose.RECOGNITION,
                source_id="card-99",
                lines=[
                    PostingLine(account_code="5110.03", debit=Decimal("500000")),
                    PostingLine(account_code="9030", credit=Decimal("500000")),
                ],
            ),
        )
        await posting.post(
            organization_id=ledger.id,
            request=PostingRequest(
                entry_date=OCTOBER,
                description="Settlement received net of commission",
                source_kind=SourceKind.PAYMENT,
                purpose=PostingPurpose.SETTLEMENT,
                source_id="card-99",
                lines=[
                    PostingLine(account_code="5110", debit=Decimal("495000")),
                    PostingLine(account_code="9420", debit=Decimal("5000")),
                    PostingLine(account_code="5110.03", credit=Decimal("500000")),
                ],
            ),
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}

        assert rows["9030"].closing_credit == Decimal("500000.00")
        assert rows["5110"].closing_debit == Decimal("495000.00")
        assert rows["9420"].closing_debit == Decimal("5000.00")
        # The transit account returns to zero, which is what makes a non-zero
        # balance there the list of unsettled card payments.
        assert "5110.03" not in rows or rows["5110.03"].closing_debit == Decimal("0")
        assert report.is_balanced

    async def test_an_advance_is_a_liability_not_revenue(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """Conflating these overstates revenue and understates obligations."""
        await posting.post(
            organization_id=ledger.id,
            request=PostingRequest(
                entry_date=OCTOBER,
                description="Advance received for future treatment",
                source_kind=SourceKind.PAYMENT,
                purpose=PostingPurpose.RECOGNITION,
                source_id="advance-1",
                lines=[
                    PostingLine(account_code="5010", debit=Decimal("1000000")),
                    PostingLine(account_code="6310", credit=Decimal("1000000")),
                ],
            ),
        )
        await db.flush()

        report = await ReportService(db).trial_balance(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        assert rows["6310"].closing_credit == Decimal("1000000.00")
        assert "9030" not in rows, "an advance must not be recognised as revenue"


class TestAccountStatement:
    async def test_running_balance_accumulates(self, ledger, posting, db: AsyncSession) -> None:
        for index, amount in enumerate(("100000", "50000", "25000")):
            await posting.post(
                organization_id=ledger.id,
                request=cash_sale(amount, source_id=f"p-{index}"),
            )
        await db.flush()

        statement = await ReportService(db).account_statement(
            organization_id=ledger.id,
            account_id=ledger.account("5010").id,
            from_date=YEAR_START,
            to_date=YEAR_END,
        )
        assert [line.running_balance for line in statement.lines] == [
            Decimal("100000.00"),
            Decimal("150000.00"),
            Decimal("175000.00"),
        ]
        assert statement.closing_balance == Decimal("175000.00")
        assert statement.total_debit == Decimal("175000.00")

    async def test_a_credit_normal_account_reports_positive(
        self, ledger, posting, db: AsyncSession
    ) -> None:
        """A liability of 500 000 shows as 500 000, not -500 000."""
        await posting.post(organization_id=ledger.id, request=cash_sale("500000"))
        await db.flush()

        statement = await ReportService(db).account_statement(
            organization_id=ledger.id,
            account_id=ledger.account("9030").id,
            from_date=YEAR_START,
            to_date=YEAR_END,
        )
        assert statement.closing_balance == Decimal("500000.00")

    async def test_a_reversal_is_flagged(self, ledger, posting, db: AsyncSession) -> None:
        entry = await posting.post(organization_id=ledger.id, request=cash_sale())
        await posting.reverse(
            organization_id=ledger.id,
            entry_id=entry.id,
            reason="Posted in error",
            reversal_date=OCTOBER,
        )
        await db.flush()

        statement = await ReportService(db).account_statement(
            organization_id=ledger.id,
            account_id=ledger.account("5010").id,
            from_date=YEAR_START,
            to_date=YEAR_END,
        )
        assert [line.is_reversal for line in statement.lines] == [False, True]
        assert statement.lines[0].reversed is True
        assert statement.closing_balance == Decimal("0")


class TestGeneralLedger:
    async def test_only_accounts_that_moved_appear(self, ledger, posting, db: AsyncSession) -> None:
        await posting.post(organization_id=ledger.id, request=cash_sale())
        await db.flush()

        pages = await ReportService(db).general_ledger(
            organization_id=ledger.id, from_date=YEAR_START, to_date=YEAR_END
        )
        assert {p.account_code for p in pages} == {"5010", "9030"}


class TestPolicyVersioning:
    """ADR-011: a policy change must never restate a posted entry."""

    async def test_a_policy_change_does_not_rewrite_history(
        self, ledger, posting, audit, db: AsyncSession
    ) -> None:
        service = PolicyService(db, audit)
        v1 = await service.create_draft(
            organization_id=ledger.id,
            name="Initial policy",
            effective_from=date(2026, 1, 1),
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=v1.id,
            operation_code="cash_service",
            description="Cash service rendered",
            debit_account_code="5010",
            credit_account_code="9030",
        )
        await service.activate(organization_id=ledger.id, policy_id=v1.id)

        rule = await posting.resolve_policy_rule(
            organization_id=ledger.id, policy_id=v1.id, operation_code="cash_service"
        )
        entry = await posting.post(
            organization_id=ledger.id,
            request=cash_sale(
                operation_code="cash_service",
                policy_id=v1.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=Decimal("300000")),
                    PostingLine(account_id=rule.credit_account_id, credit=Decimal("300000")),
                ],
            ),
        )
        await db.flush()
        original_lines = [
            (line.account_id, line.debit, line.credit)
            for line in await posting.get_lines(organization_id=ledger.id, entry_id=entry.id)
        ]

        # A new version remaps the same operation to different accounts.
        v2 = await service.create_draft(
            organization_id=ledger.id,
            name="Revised policy",
            effective_from=date(2026, 11, 1),
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=v2.id,
            operation_code="cash_service",
            description="Cash service rendered (revised)",
            debit_account_code="5110",
            credit_account_code="6310",
        )
        await service.activate(organization_id=ledger.id, policy_id=v2.id)
        await db.flush()

        # The already-posted entry is untouched.
        after = [
            (line.account_id, line.debit, line.credit)
            for line in await posting.get_lines(organization_id=ledger.id, entry_id=entry.id)
        ]
        assert after == original_lines
        assert entry.policy_id == v1.id

    async def test_activating_supersedes_the_previous_version(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        service = PolicyService(db, audit)
        v1 = await service.create_draft(
            organization_id=ledger.id, name="v1", effective_from=date(2026, 1, 1)
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=v1.id,
            operation_code="op",
            description="x",
            debit_account_code="5010",
            credit_account_code="9030",
        )
        await service.activate(organization_id=ledger.id, policy_id=v1.id)

        v2 = await service.create_draft(
            organization_id=ledger.id, name="v2", effective_from=date(2026, 7, 1)
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=v2.id,
            operation_code="op",
            description="y",
            debit_account_code="5110",
            credit_account_code="9030",
        )
        await service.activate(organization_id=ledger.id, policy_id=v2.id)
        await db.flush()

        assert v1.status is PolicyStatus.SUPERSEDED
        assert v1.effective_to == date(2026, 6, 30)
        assert v2.status is PolicyStatus.ACTIVE

        # Resolution is by effective date, so a back-dated posting uses the
        # rules that applied then — even though that version is superseded now.
        june = await service.policy_in_force_on(organization_id=ledger.id, on=date(2026, 6, 15))
        august = await service.policy_in_force_on(organization_id=ledger.id, on=date(2026, 8, 15))
        assert june.id == v1.id, "a superseded version still answers for its own window"
        assert august.id == v2.id

    async def test_a_version_cannot_start_before_the_one_it_supersedes(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        """Found by driving the policy screen in a browser.

        Superseding closes the prior version "the day before" the new one
        starts. If the new one starts first, that date lands before the prior
        version began, and the check constraint refuses the UPDATE — as a 500,
        long after the person could have been told what was wrong.
        """
        service = PolicyService(db, audit)

        async def version(name: str, on: date):
            policy = await service.create_draft(
                organization_id=ledger.id, name=name, effective_from=on
            )
            await service.set_rule(
                organization_id=ledger.id,
                policy_id=policy.id,
                operation_code="op",
                description=name,
                debit_account_code="5010",
                credit_account_code="9030",
            )
            return policy

        current = await version("in force", date(2026, 7, 1))
        await service.activate(organization_id=ledger.id, policy_id=current.id)

        backdated = await version("back-dated", date(2026, 3, 1))
        with pytest.raises(ValidationFailed, match="must start after the one it replaces"):
            await service.activate(organization_id=ledger.id, policy_id=backdated.id)

        # The refusal leaves both versions as they were, so the books keep
        # answering to the version that was already in force.
        await db.flush()
        assert current.status is PolicyStatus.ACTIVE
        assert current.effective_to is None
        assert backdated.status is PolicyStatus.DRAFT

    async def test_a_version_cannot_start_on_the_same_day_as_the_one_in_force(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        """Two versions answering for one date is the thing versioning exists
        to prevent, and the day-before arithmetic cannot express it."""
        service = PolicyService(db, audit)

        async def version(name: str, on: date):
            policy = await service.create_draft(
                organization_id=ledger.id, name=name, effective_from=on
            )
            await service.set_rule(
                organization_id=ledger.id,
                policy_id=policy.id,
                operation_code="op",
                description=name,
                debit_account_code="5010",
                credit_account_code="9030",
            )
            return policy

        first = await version("first", date(2026, 7, 1))
        await service.activate(organization_id=ledger.id, policy_id=first.id)
        same_day = await version("same day", date(2026, 7, 1))

        with pytest.raises(ValidationFailed, match="in force from 2026-07-01"):
            await service.activate(organization_id=ledger.id, policy_id=same_day.id)

    async def test_a_draft_can_be_discarded(self, ledger, audit, db: AsyncSession) -> None:
        """Found by driving the policy screen in a browser.

        A draft's effective date cannot be edited, so a draft that starts
        before the version in force can never be activated — and while it
        exists no new version can be created. Without a way to throw it
        away the editor is a dead end.
        """
        service = PolicyService(db, audit)
        draft = await service.create_draft(
            organization_id=ledger.id, name="mistaken", effective_from=date(2026, 1, 1)
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=draft.id,
            operation_code="op",
            description="x",
            debit_account_code="5010",
            credit_account_code="9030",
        )
        await db.flush()

        await service.discard_draft(organization_id=ledger.id, policy_id=draft.id)
        await db.flush()

        assert await service.list_policies(ledger.id) == []
        # Its rules go with it; an orphaned rule would have no policy to
        # answer for and would keep the foreign key alive.
        remaining = (
            await db.execute(
                text("SELECT count(*) FROM policy_rule WHERE policy_id = :id"),
                {"id": draft.id},
            )
        ).scalar_one()
        assert remaining == 0

        # And the way out is now open: a new version can be created.
        replacement = await service.create_draft(
            organization_id=ledger.id, name="corrected", effective_from=date(2027, 1, 1)
        )
        assert replacement.version == 1

    async def test_discarding_is_audited_before_the_row_goes(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        """The record of what was thrown away outlives the thing itself."""
        service = PolicyService(db, audit)
        draft = await service.create_draft(
            organization_id=ledger.id, name="mistaken", effective_from=date(2026, 1, 1)
        )
        await service.discard_draft(organization_id=ledger.id, policy_id=draft.id)
        await db.flush()

        row = (
            await db.execute(
                text(
                    "SELECT old_values::text FROM audit_log "
                    "WHERE action = 'accounting.policy.discarded'"
                )
            )
        ).scalar_one()
        assert '"version": 1' in row

    async def test_an_active_policy_cannot_be_discarded(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        """Deleting a version the books posted under would leave entries
        pointing at a policy that no longer exists."""
        service = PolicyService(db, audit)
        policy = await service.create_draft(
            organization_id=ledger.id, name="v1", effective_from=date(2026, 1, 1)
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=policy.id,
            operation_code="op",
            description="x",
            debit_account_code="5010",
            credit_account_code="9030",
        )
        await service.activate(organization_id=ledger.id, policy_id=policy.id)
        await db.flush()

        with pytest.raises(Conflict, match="cannot be discarded"):
            await service.discard_draft(organization_id=ledger.id, policy_id=policy.id)

    async def test_no_policy_in_force_before_the_first_version(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        from app.core.errors import NotFound

        service = PolicyService(db, audit)
        policy = await service.create_draft(
            organization_id=ledger.id, name="v1", effective_from=date(2026, 6, 1)
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=policy.id,
            operation_code="op",
            description="x",
            debit_account_code="5010",
            credit_account_code="9030",
        )
        await service.activate(organization_id=ledger.id, policy_id=policy.id)

        with pytest.raises(NotFound, match="was in force"):
            await service.policy_in_force_on(organization_id=ledger.id, on=date(2026, 1, 15))

    async def test_a_draft_is_never_in_force(self, ledger, audit, db: AsyncSession) -> None:
        from app.core.errors import NotFound

        service = PolicyService(db, audit)
        await service.create_draft(
            organization_id=ledger.id, name="draft", effective_from=date(2026, 1, 1)
        )
        with pytest.raises(NotFound):
            await service.policy_in_force_on(organization_id=ledger.id, on=date(2026, 6, 15))

    async def test_an_active_policy_cannot_be_edited(self, ledger, audit, db: AsyncSession) -> None:
        service = PolicyService(db, audit)
        policy = await service.create_draft(
            organization_id=ledger.id, name="v1", effective_from=date(2026, 1, 1)
        )
        await service.set_rule(
            organization_id=ledger.id,
            policy_id=policy.id,
            operation_code="op",
            description="x",
            debit_account_code="5010",
            credit_account_code="9030",
        )
        await service.activate(organization_id=ledger.id, policy_id=policy.id)

        with pytest.raises(Conflict, match="cannot be edited"):
            await service.set_rule(
                organization_id=ledger.id,
                policy_id=policy.id,
                operation_code="op",
                description="changed",
                debit_account_code="5110",
                credit_account_code="9030",
            )

    async def test_a_rule_pointing_at_a_structural_account_is_refused(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        service = PolicyService(db, audit)
        policy = await service.create_draft(
            organization_id=ledger.id, name="v1", effective_from=date(2026, 1, 1)
        )
        with pytest.raises(ValidationFailed, match="cannot be posted to"):
            await service.set_rule(
                organization_id=ledger.id,
                policy_id=policy.id,
                operation_code="op",
                description="x",
                debit_account_code="5000",  # a GROUP
                credit_account_code="9030",
            )

    async def test_an_empty_policy_cannot_be_activated(
        self, ledger, audit, db: AsyncSession
    ) -> None:
        service = PolicyService(db, audit)
        policy = await service.create_draft(
            organization_id=ledger.id, name="empty", effective_from=date(2026, 1, 1)
        )
        with pytest.raises(ValidationFailed, match="no rules"):
            await service.activate(organization_id=ledger.id, policy_id=policy.id)


class TestDefaultPolicySeed:
    """The starting policy derived from the legacy workbook.

    Uses a bare organization rather than the `ledger` fixture: seeding the
    statutory chart refuses when a chart already exists, which is itself the
    point of that guard.
    """

    async def test_it_creates_a_draft_not_an_active_policy(
        self, org_a, audit, db: AsyncSession
    ) -> None:
        """Nothing posts under it until a person activates it."""
        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.default_policy import (
            DEFAULT_RULES,
            DefaultPolicyService,
        )

        await AccountService(db, audit).seed_statutory_chart(organization_id=org_a.id)
        policy = await DefaultPolicyService(db, audit).seed(
            organization_id=org_a.id, effective_from=date(2026, 1, 1)
        )
        assert policy.status is PolicyStatus.DRAFT

        rules = await PolicyService(db, audit).list_rules(
            organization_id=org_a.id, policy_id=policy.id
        )
        assert len(rules) == len(DEFAULT_RULES)
        # Every rule records where in the workbook it came from, so a reviewer
        # can check it rather than take it on trust.
        assert all(r.required_documents for r in rules)

    async def test_it_refuses_without_a_chart_of_accounts(
        self, org_a, audit, db: AsyncSession
    ) -> None:
        from app.modules.accounting.services.default_policy import DefaultPolicyService

        with pytest.raises(ValidationFailed, match="do not exist yet"):
            await DefaultPolicyService(db, audit).seed(
                organization_id=org_a.id, effective_from=date(2026, 1, 1)
            )

    async def test_it_refuses_when_a_policy_is_already_in_force(
        self, org_a, audit, db: AsyncSession
    ) -> None:
        """Re-running the command must not leave a confusing duplicate draft."""
        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.default_policy import DefaultPolicyService

        await AccountService(db, audit).seed_statutory_chart(organization_id=org_a.id)
        service = DefaultPolicyService(db, audit)
        policy = await service.seed(organization_id=org_a.id, effective_from=date(2026, 1, 1))
        await PolicyService(db, audit).activate(organization_id=org_a.id, policy_id=policy.id)

        with pytest.raises(Conflict, match="already"):
            await service.seed(organization_id=org_a.id, effective_from=date(2026, 6, 1))

    async def test_the_rules_only_reference_postable_accounts(
        self, org_a, audit, db: AsyncSession
    ) -> None:
        """A rule pointing at a structural account could never post."""
        from app.modules.accounting.services.accounts import AccountService
        from app.modules.accounting.services.default_policy import (
            DEFAULT_RULES,
            DefaultPolicyService,
        )

        accounts = AccountService(db, audit)
        await accounts.seed_statutory_chart(organization_id=org_a.id)
        await DefaultPolicyService(db, audit).seed(
            organization_id=org_a.id, effective_from=date(2026, 1, 1)
        )
        for code in {r.debit for r in DEFAULT_RULES} | {r.credit for r in DEFAULT_RULES}:
            account = await accounts.get_by_code(organization_id=org_a.id, code=code)
            assert account is not None, code
            assert account.is_postable, code
