"""A payroll run end to end: calculated, approved, posted, paid.

The tests that matter most here are about what the system *refuses*. A run
whose statutory rules are not approved must not post, and must say which
rule and which open question stops it — at the time of writing that is the
social tax rate, unverified during the workbook audit. An approved run must
not change when a new rule is approved afterwards. And the person who
calculates a payroll must not be the one who approves it.

The accounting assertions check the ledger itself, not the API's reply: the
trial balance before and after, by account code.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.hr.enums import AttendanceCode
from app.modules.payroll.enums import (
    AdjustmentKind,
    RuleMethod,
    StatutoryRuleKind,
)

MARCH_START = date(2026, 3, 1)
MARCH_END = date(2026, 3, 31)
WORKING_DAYS = 22


async def an_employee(employees, org, *, family="Xodimov", given="Ishchi", hired=None):
    return await employees.create(
        organization_id=org.id,
        family_name=family,
        given_name=given,
        hired_on=hired or date(2025, 1, 1),
    )


async def approved_rule(rules, org, kind, rate, *, actor, effective_from=date(2019, 1, 1),
                        effective_to=None, component_of=None):
    rule = await rules.propose(
        organization_id=org.id,
        kind=kind,
        method=RuleMethod.PERCENT_OF_GROSS,
        rate=Decimal(rate),
        effective_from=effective_from,
        effective_to=effective_to,
        component_of=component_of,
        legal_reference=f"Test reference for {kind.value}",
    )
    return await rules.approve(organization_id=org.id, rule_id=rule.id, actor_user_id=actor)


async def worked_days(attendance, org, employee, count: int) -> None:
    """Mark `count` working days in March 2026, starting from the 2nd."""
    for offset in range(count):
        await attendance.record(
            organization_id=org.id,
            employee_id=employee.id,
            on=date(2026, 3, 2 + offset),
            code=AttendanceCode.WORKED,
            hours=Decimal("8"),
        )


async def a_run(payroll, org, *, working_days=WORKING_DAYS):
    return await payroll.open_run(
        organization_id=org.id, year=2026, month=3, working_days=working_days
    )


async def balances(db, organization_id, codes) -> dict[str, Decimal]:
    report = await ReportService(db).trial_balance(
        organization_id=organization_id,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 12, 31),
    )
    out = {code: Decimal("0") for code in codes}
    for row in report.rows:
        if row.account_code in out:
            out[row.account_code] = row.closing_debit - row.closing_credit
    return out


class TestTheGateThatBlocksPosting:
    async def test_a_run_with_no_approved_rules_says_what_is_missing(
        self, payroll, employees, compensation, attendance, sales
    ) -> None:
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        run = await a_run(payroll, sales)
        run = await payroll.calculate(organization_id=sales.id, run_id=run.id)

        blocked = json.loads(run.blocked_reasons or "[]")
        assert len(blocked) == 2
        assert any("income tax" in reason for reason in blocked)
        assert any("social tax" in reason for reason in blocked)
        assert any("OPEN_QUESTIONS P2" in reason for reason in blocked)

    async def test_approval_is_refused_while_a_rule_is_unapproved(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        """The social tax rate is the one the audit could not confirm."""
        owner = org_a.owner.id
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        await approved_rule(
            payroll_rules, sales, StatutoryRuleKind.INCOME_TAX, "12", actor=owner
        )

        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id)
        with pytest.raises(Conflict) as refusal:
            await payroll.approve(
                organization_id=sales.id,
                run_id=run.id,
                actor_user_id=org_a.members["accountant"][0].id,
            )
        assert "social tax" in str(refusal.value)
        assert "P2" in str(refusal.value)

    async def test_a_draft_rule_is_not_an_approved_one(
        self, payroll, payroll_rules, employees, compensation, attendance, sales
    ) -> None:
        await payroll_rules.propose(
            organization_id=sales.id,
            kind=StatutoryRuleKind.SOCIAL_TAX,
            method=RuleMethod.PERCENT_OF_GROSS,
            rate=Decimal("12"),
            effective_from=date(2019, 1, 1),
            legal_reference="Written down, not approved",
        )
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        run = await a_run(payroll, sales)
        run = await payroll.calculate(organization_id=sales.id, run_id=run.id)
        blocked = json.loads(run.blocked_reasons or "[]")
        assert any("social tax" in reason for reason in blocked)

    async def test_a_rate_cannot_be_approved_without_its_authority(
        self, payroll_rules, sales, org_a
    ) -> None:
        rule = await payroll_rules.propose(
            organization_id=sales.id,
            kind=StatutoryRuleKind.SOCIAL_TAX,
            method=RuleMethod.PERCENT_OF_GROSS,
            rate=Decimal("12"),
            effective_from=date(2019, 1, 1),
        )
        with pytest.raises(ValidationFailed) as refusal:
            await payroll_rules.approve(
                organization_id=sales.id, rule_id=rule.id, actor_user_id=org_a.owner.id
            )
        assert "authority it comes from" in str(refusal.value)


class TestAFullRun:
    @pytest.fixture
    async def ready(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ):
        """One employee, 11 of 22 days, every rule approved."""
        owner = org_a.owner.id
        for kind, rate, component in (
            (StatutoryRuleKind.INCOME_TAX, "12", None),
            (StatutoryRuleKind.INPS, "0.1", StatutoryRuleKind.INCOME_TAX),
            (StatutoryRuleKind.SOCIAL_TAX, "12", None),
        ):
            await approved_rule(
                payroll_rules, sales, kind, rate, actor=owner, component_of=component
            )
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        run = await a_run(payroll, sales)
        return run, employee

    async def test_the_payslip_matches_the_figures_worked_out_by_hand(
        self, payroll, sales, ready
    ) -> None:
        """2,000,000 x 11 / 22 = 1,000,000 gross; 12% = 120,000; net 880,000."""
        run, _employee = ready
        await payroll.calculate(organization_id=sales.id, run_id=run.id)
        views = await payroll.payslips(organization_id=sales.id, run_id=run.id)
        assert len(views) == 1
        slip = views[0].payslip
        assert slip.gross == Decimal("1000000.00")
        assert slip.employee_deductions == Decimal("120000.00")
        assert slip.employer_contributions == Decimal("120000.00")
        assert slip.net == Decimal("880000.00")
        assert slip.worked_days == Decimal("11")

    async def test_every_line_names_the_rule_version_behind_it(
        self, payroll, sales, ready
    ) -> None:
        run, _ = ready
        await payroll.calculate(organization_id=sales.id, run_id=run.id)
        views = await payroll.payslips(organization_id=sales.id, run_id=run.id)
        statutory = [line for line in views[0].lines if line.code != "SALARY"]
        assert statutory
        assert all(line.statutory_rule_id is not None for line in statutory)
        assert all(line.rate is not None for line in statutory)

    async def test_the_person_who_calculated_cannot_approve(
        self, payroll, sales, ready, org_a
    ) -> None:
        run, _ = ready
        actor = org_a.owner.id
        await payroll.calculate(organization_id=sales.id, run_id=run.id, actor_user_id=actor)
        with pytest.raises(Conflict) as refusal:
            await payroll.approve(organization_id=sales.id, run_id=run.id, actor_user_id=actor)
        assert "cannot approve" in str(refusal.value)

    async def test_posting_moves_the_ledger_by_the_right_amounts(
        self, payroll, sales, ready, org_a, db
    ) -> None:
        """Expense is gross plus the employer's tax; the debts are the rest.

        By hand: 9420 takes 1,000,000 + 120,000 = 1,120,000. The employee is
        owed 880,000 on 6710. The budget is owed 119,000 income tax on 6411,
        1,000 INPS on 6520.02, and 120,000 social tax on 6520.01.
        """
        run, _ = ready
        codes = ["9420", "6710", "6411", "6520.01", "6520.02"]
        before = await balances(db, sales.id, codes)

        await payroll.calculate(
            organization_id=sales.id, run_id=run.id, actor_user_id=org_a.owner.id
        )
        await payroll.approve(
            organization_id=sales.id,
            run_id=run.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        await payroll.post(
            organization_id=sales.id,
            run_id=run.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        after = await balances(db, sales.id, codes)

        assert after["9420"] - before["9420"] == Decimal("1120000.00")
        assert after["6710"] - before["6710"] == Decimal("-880000.00")
        assert after["6411"] - before["6411"] == Decimal("-119000.00")
        assert after["6520.02"] - before["6520.02"] == Decimal("-1000.00")
        assert after["6520.01"] - before["6520.01"] == Decimal("-120000.00")

    async def test_paying_settles_the_debt_and_recognises_no_expense(
        self, payroll, sales, ready, org_a, db
    ) -> None:
        run, _ = ready
        accountant = org_a.members["accountant"][0].id
        await payroll.calculate(
            organization_id=sales.id, run_id=run.id, actor_user_id=org_a.owner.id
        )
        await payroll.approve(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        await payroll.post(organization_id=sales.id, run_id=run.id, actor_user_id=accountant)

        codes = ["9420", "6710", "5110.01"]
        before = await balances(db, sales.id, codes)
        await payroll.mark_paid(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        after = await balances(db, sales.id, codes)

        # The debt to the employee is cleared; the bank falls; no expense moves.
        assert after["6710"] - before["6710"] == Decimal("880000.00")
        assert after["5110.01"] - before["5110.01"] == Decimal("-880000.00")
        assert after["9420"] == before["9420"]

    async def test_a_run_cannot_be_posted_twice(
        self, payroll, sales, ready, org_a
    ) -> None:
        run, _ = ready
        accountant = org_a.members["accountant"][0].id
        await payroll.calculate(
            organization_id=sales.id, run_id=run.id, actor_user_id=org_a.owner.id
        )
        await payroll.approve(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        await payroll.post(organization_id=sales.id, run_id=run.id, actor_user_id=accountant)
        with pytest.raises(Conflict):
            await payroll.post(
                organization_id=sales.id, run_id=run.id, actor_user_id=accountant
            )

    async def test_the_expense_equals_gross_plus_employer_cost(
        self, payroll, sales, ready, org_a, db
    ) -> None:
        """What the clinic spent on employing somebody, in one figure.

        The reporting module reads the same ledger, so a profit and loss for
        the period must show exactly this and nothing more.
        """
        run, _ = ready
        accountant = org_a.members["accountant"][0].id
        await payroll.calculate(
            organization_id=sales.id, run_id=run.id, actor_user_id=org_a.owner.id
        )
        await payroll.approve(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        await payroll.post(organization_id=sales.id, run_id=run.id, actor_user_id=accountant)

        from app.modules.reporting.services.statements import StatementService

        statements = StatementService(db, payroll._audit)
        profit = await statements.profit_and_loss(
            organization_id=sales.id, from_date=MARCH_START, to_date=MARCH_END
        )
        assert profit.expenses.total == Decimal("1120000.00")
        assert profit.net_result == Decimal("-1120000.00")

    async def test_the_balance_sheet_still_balances_afterwards(
        self, payroll, sales, ready, org_a, db
    ) -> None:
        run, _ = ready
        accountant = org_a.members["accountant"][0].id
        await payroll.calculate(
            organization_id=sales.id, run_id=run.id, actor_user_id=org_a.owner.id
        )
        await payroll.approve(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        await payroll.post(organization_id=sales.id, run_id=run.id, actor_user_id=accountant)
        await payroll.mark_paid(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )

        from app.modules.reporting.services.statements import StatementService

        sheet = await StatementService(db, payroll._audit).balance_sheet(
            organization_id=sales.id, as_of=MARCH_END
        )
        assert sheet.balances, f"difference {sheet.difference}"


class TestHistoryStaysPut:
    async def test_a_rule_approved_later_does_not_change_an_approved_run(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        """The property the workbook could not offer.

        Its sheet recomputes every month from today's formulas, so editing
        one cell rewrote three months of 2023. Here the payslip holds its own
        amounts, and approving a new social tax rate afterwards leaves them
        exactly as they were.
        """
        owner = org_a.owner.id
        accountant = org_a.members["accountant"][0].id
        for kind, rate, component in (
            (StatutoryRuleKind.INCOME_TAX, "12", None),
            (StatutoryRuleKind.INPS, "0.1", StatutoryRuleKind.INCOME_TAX),
            (StatutoryRuleKind.SOCIAL_TAX, "12", None),
        ):
            await approved_rule(
                payroll_rules, sales, kind, rate, actor=owner, component_of=component
            )
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id, actor_user_id=owner)
        await payroll.approve(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        before = (await payroll.payslips(organization_id=sales.id, run_id=run.id))[0].payslip
        recorded = (
            before.gross,
            before.employee_deductions,
            before.employer_contributions,
            before.net,
        )

        # A new social tax rate arrives, effective from a later date.
        await approved_rule(
            payroll_rules,
            sales,
            StatutoryRuleKind.SOCIAL_TAX,
            "25",
            actor=owner,
            effective_from=date(2026, 6, 1),
        )
        after = (await payroll.payslips(organization_id=sales.id, run_id=run.id))[0].payslip
        assert (
            after.gross,
            after.employee_deductions,
            after.employer_contributions,
            after.net,
        ) == recorded

    async def test_an_approved_run_cannot_be_recalculated(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        owner = org_a.owner.id
        accountant = org_a.members["accountant"][0].id
        for kind, rate, component in (
            (StatutoryRuleKind.INCOME_TAX, "12", None),
            (StatutoryRuleKind.SOCIAL_TAX, "12", None),
        ):
            await approved_rule(
                payroll_rules, sales, kind, rate, actor=owner, component_of=component
            )
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id, actor_user_id=owner)
        await payroll.approve(
            organization_id=sales.id, run_id=run.id, actor_user_id=accountant
        )
        with pytest.raises(Conflict) as refusal:
            await payroll.calculate(organization_id=sales.id, run_id=run.id)
        assert "settled" in str(refusal.value)

    async def test_approving_a_rule_closes_the_one_it_replaces(
        self, payroll_rules, sales, org_a
    ) -> None:
        owner = org_a.owner.id
        first = await approved_rule(
            payroll_rules, sales, StatutoryRuleKind.SOCIAL_TAX, "12", actor=owner
        )
        await approved_rule(
            payroll_rules,
            sales,
            StatutoryRuleKind.SOCIAL_TAX,
            "25",
            actor=owner,
            effective_from=date(2026, 6, 1),
        )
        assert first.effective_to == date(2026, 5, 31)


class TestAdjustments:
    async def test_an_adjustment_without_a_reason_is_refused(
        self, payroll, employees, sales
    ) -> None:
        employee = await an_employee(employees, sales)
        run = await a_run(payroll, sales)
        with pytest.raises(ValidationFailed):
            await payroll.add_adjustment(
                organization_id=sales.id,
                run_id=run.id,
                employee_id=employee.id,
                kind=AdjustmentKind.BONUS,
                amount=Decimal("100000"),
                reason="   ",
            )

    async def test_an_adjustment_is_audited_with_its_reason(
        self, payroll, employees, sales, db, org_a
    ) -> None:
        """The control the workbook's nine undocumented corrections lacked."""
        employee = await an_employee(employees, sales)
        run = await a_run(payroll, sales)
        await payroll.add_adjustment(
            organization_id=sales.id,
            run_id=run.id,
            employee_id=employee.id,
            kind=AdjustmentKind.BONUS,
            amount=Decimal("100000"),
            reason="Agreed with the owner for covering a colleague's leave",
            actor_user_id=org_a.owner.id,
        )
        await db.flush()
        rows = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == sales.id,
                    AuditLog.action == events.PAYROLL_ADJUSTED,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert "covering a colleague" in rows[0].new_values["reason"]

    async def test_an_adjustment_cannot_be_added_to_a_settled_run(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        owner = org_a.owner.id
        for kind, rate in (
            (StatutoryRuleKind.INCOME_TAX, "12"),
            (StatutoryRuleKind.SOCIAL_TAX, "12"),
        ):
            await approved_rule(payroll_rules, sales, kind, rate, actor=owner)
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )
        await worked_days(attendance, sales, employee, 11)
        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id, actor_user_id=owner)
        await payroll.approve(
            organization_id=sales.id,
            run_id=run.id,
            actor_user_id=org_a.members["accountant"][0].id,
        )
        with pytest.raises(Conflict):
            await payroll.add_adjustment(
                organization_id=sales.id,
                run_id=run.id,
                employee_id=employee.id,
                kind=AdjustmentKind.DEDUCTION,
                amount=Decimal("1000"),
                reason="Too late for this run",
            )


class TestOneRunPerMonth:
    async def test_a_second_run_for_the_same_month_is_refused(
        self, payroll, sales
    ) -> None:
        await a_run(payroll, sales)
        with pytest.raises(Conflict) as refusal:
            await a_run(payroll, sales)
        assert "already exists" in str(refusal.value)

    async def test_a_run_needs_a_working_day_count(self, payroll, sales) -> None:
        with pytest.raises(ValidationFailed):
            await payroll.open_run(
                organization_id=sales.id, year=2026, month=4, working_days=0
            )


class TestNothingCumulativeToReset:
    async def test_january_computes_the_same_as_december(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        """A flat rate has no year-to-date, so no year-end reset exists.

        The workbook needed one — its tax was cumulative — and implemented it
        by multiplying the previous row by zero every January, which is
        defect D3. Here the question cannot arise, and this test is what says
        so.
        """
        owner = org_a.owner.id
        for kind, rate, component in (
            (StatutoryRuleKind.INCOME_TAX, "12", None),
            (StatutoryRuleKind.INPS, "0.1", StatutoryRuleKind.INCOME_TAX),
            (StatutoryRuleKind.SOCIAL_TAX, "12", None),
        ):
            await approved_rule(
                payroll_rules, sales, kind, rate, actor=owner, component_of=component
            )
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2025, 1, 1),
        )

        results = []
        for year, month in ((2025, 11), (2026, 1)):
            for offset in range(11):
                await attendance.record(
                    organization_id=sales.id,
                    employee_id=employee.id,
                    on=date(year, month, 2 + offset),
                    code=AttendanceCode.WORKED,
                    hours=Decimal("8"),
                )
            run = await payroll.open_run(
                organization_id=sales.id, year=year, month=month, working_days=22
            )
            await payroll.calculate(organization_id=sales.id, run_id=run.id)
            slip = (await payroll.payslips(organization_id=sales.id, run_id=run.id))[0].payslip
            results.append((slip.gross, slip.employee_deductions, slip.net))

        assert results[0] == results[1]
        assert results[0] == (
            Decimal("1000000.00"),
            Decimal("120000.00"),
            Decimal("880000.00"),
        )


class TestTenantIsolation:
    async def test_one_clinic_s_rules_do_not_reach_another(
        self, payroll_rules, sales, org_b, org_a
    ) -> None:
        await approved_rule(
            payroll_rules, sales, StatutoryRuleKind.INCOME_TAX, "12", actor=org_a.owner.id
        )
        rules = await payroll_rules.rules_in_force(
            organization_id=org_b.id, on_date=MARCH_END
        )
        assert rules == []

    async def test_a_run_cannot_be_read_across_tenants(
        self, payroll, sales, org_b
    ) -> None:
        run = await a_run(payroll, sales)
        from app.core.errors import NotFound

        with pytest.raises(NotFound):
            await payroll.get(organization_id=org_b.id, run_id=run.id)


class TestTheSeededDrafts:
    async def test_only_verified_rates_are_proposed_and_none_is_approved(
        self, payroll_rules, sales
    ) -> None:
        """The social tax is deliberately absent: its rate is unconfirmed."""
        created = await payroll_rules.seed_verified_drafts(organization_id=sales.id)
        kinds = {rule.kind for rule in created}
        assert StatutoryRuleKind.INCOME_TAX in kinds
        assert StatutoryRuleKind.INPS in kinds
        assert StatutoryRuleKind.EMPLOYEE_PENSION in kinds
        assert StatutoryRuleKind.SOCIAL_TAX not in kinds
        assert all(rule.status.value == "DRAFT" for rule in created)
        assert all(rule.legal_reference for rule in created)

    async def test_seeding_twice_proposes_nothing_new(
        self, payroll_rules, sales
    ) -> None:
        await payroll_rules.seed_verified_drafts(organization_id=sales.id)
        again = await payroll_rules.seed_verified_drafts(organization_id=sales.id)
        assert again == []

    async def test_the_seeded_pension_rule_applies_to_no_current_period(
        self, payroll_rules, sales, org_a
    ) -> None:
        created = await payroll_rules.seed_verified_drafts(organization_id=sales.id)
        pension = next(
            rule for rule in created if rule.kind is StatutoryRuleKind.EMPLOYEE_PENSION
        )
        await payroll_rules.approve(
            organization_id=sales.id, rule_id=pension.id, actor_user_id=org_a.owner.id
        )
        in_force = await payroll_rules.rules_in_force(
            organization_id=sales.id, on_date=MARCH_END
        )
        assert all(rule.kind is not StatutoryRuleKind.EMPLOYEE_PENSION for rule in in_force)


class TestSalaryChangeDuringAPeriod:
    async def test_days_are_counted_on_each_side_of_the_change(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        """Five days at the old salary, six at the new, from the timesheet.

        By hand: 1,000,000 x 5 / 22 = 227,272.7272... -> 227,272.73, and
        2,000,000 x 6 / 22 = 545,454.5454... -> 545,454.55. Gross 772,727.28.
        """
        owner = org_a.owner.id
        for kind, rate, component in (
            (StatutoryRuleKind.INCOME_TAX, "12", None),
            (StatutoryRuleKind.INPS, "0.1", StatutoryRuleKind.INCOME_TAX),
            (StatutoryRuleKind.SOCIAL_TAX, "12", None),
        ):
            await approved_rule(
                payroll_rules, sales, kind, rate, actor=owner, component_of=component
            )
        employee = await an_employee(employees, sales)
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("1000000"),
            effective_from=date(2025, 1, 1),
        )
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2026, 3, 10),
            note="Agreed rise from the tenth",
        )
        # Five worked days before the rise, six after.
        for day in (2, 3, 4, 5, 6):
            await attendance.record(
                organization_id=sales.id,
                employee_id=employee.id,
                on=date(2026, 3, day),
                code=AttendanceCode.WORKED,
                hours=Decimal("8"),
            )
        for day in (10, 11, 12, 13, 16, 17):
            await attendance.record(
                organization_id=sales.id,
                employee_id=employee.id,
                on=date(2026, 3, day),
                code=AttendanceCode.WORKED,
                hours=Decimal("8"),
            )

        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id)
        view = (await payroll.payslips(organization_id=sales.id, run_id=run.id))[0]
        earnings = sorted(
            line.amount for line in view.lines if line.code == "SALARY"
        )
        assert earnings == [Decimal("227272.73"), Decimal("545454.55")]
        assert view.payslip.gross == Decimal("772727.28")
        assert view.payslip.worked_days == Decimal("11")


class TestWhoIsOnTheRun:
    async def test_somebody_hired_after_the_period_is_not_paid(
        self, payroll, payroll_rules, employees, compensation, attendance, sales, org_a
    ) -> None:
        await approved_rule(
            payroll_rules, sales, StatutoryRuleKind.INCOME_TAX, "12", actor=org_a.owner.id
        )
        employee = await an_employee(
            employees, sales, hired=date(2026, 5, 1), family="Kelajak"
        )
        await compensation.set_salary(
            organization_id=sales.id,
            employee_id=employee.id,
            amount=Decimal("2000000"),
            effective_from=date(2026, 5, 1),
        )
        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id)
        assert await payroll.payslips(organization_id=sales.id, run_id=run.id) == []

    async def test_somebody_with_no_agreed_salary_is_not_paid(
        self, payroll, employees, sales
    ) -> None:
        await an_employee(employees, sales)
        run = await a_run(payroll, sales)
        await payroll.calculate(organization_id=sales.id, run_id=run.id)
        assert await payroll.payslips(organization_id=sales.id, run_id=run.id) == []
