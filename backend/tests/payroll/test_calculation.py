"""Payslip arithmetic, checked against figures worked out independently.

Every expected number in this file was calculated by hand and is written as
a literal, not derived from the code under test. That is the only way a
calculation test means anything: a test that recomputes the implementation's
own formula passes whatever the formula says.

The cases are the ones the brief lists, plus the two the workbook audit
turned up — the 12.1% that one employee was withheld through 2019, and a
zero denominator that the spreadsheet never met because somebody typed the
divisor every month.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.modules.payroll.enums import (
    AdjustmentKind,
    PayslipLineCategory,
    RuleMethod,
    StatutoryRuleKind,
)
from app.modules.payroll.services.calculation import (
    AdjustmentInput,
    RuleInput,
    SalarySpan,
    compute_payslip,
    missing_rule_kinds,
)

MARCH = date(2026, 3, 31)


def rule(
    kind: StatutoryRuleKind,
    rate: str,
    *,
    component_of: StatutoryRuleKind | None = None,
    effective_from: date = date(2019, 1, 1),
    effective_to: date | None = None,
) -> RuleInput:
    return RuleInput(
        id=uuid.uuid4(),
        kind=kind,
        method=RuleMethod.PERCENT_OF_GROSS,
        rate=Decimal(rate),
        fixed_amount=None,
        component_of=component_of,
        effective_from=effective_from,
        effective_to=effective_to,
    )


#: The rules verified by the workbook audit for 2019-01-01 onward, plus a
#: social tax rate of 12% used only as a *test* figure — the real one is
#: unconfirmed (OPEN_QUESTIONS P2) and nothing is seeded with it.
def current_rules() -> list[RuleInput]:
    return [
        rule(StatutoryRuleKind.INCOME_TAX, "12"),
        rule(StatutoryRuleKind.INPS, "0.1", component_of=StatutoryRuleKind.INCOME_TAX),
        rule(StatutoryRuleKind.SOCIAL_TAX, "12"),
    ]


def span(salary: str, worked: str) -> SalarySpan:
    return SalarySpan(
        compensation_id=uuid.uuid4(),
        monthly_salary=Decimal(salary),
        worked_days=Decimal(worked),
    )


def amount_of(result, code: str) -> Decimal:
    return next(line.amount for line in result.lines if line.code == code)


class TestProration:
    def test_the_briefs_own_example(self) -> None:
        """2,000,000 a month, 13 of 26 days worked.

        Worked out by hand: 13 / 26 = 0.5, so gross is 1,000,000. Income tax
        is 12% of that = 120,000, of which 0.1% = 1,000 is INPS, leaving
        119,000 to the budget. Net is 1,000,000 - 120,000 = 880,000. The
        employer's social tax of 120,000 is *not* part of that subtraction.
        """
        result = compute_payslip(
            spans=[span("2000000", "13")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
        )
        assert result.gross == Decimal("1000000.00")
        assert amount_of(result, "INCOME_TAX") == Decimal("119000.00")
        assert amount_of(result, "INPS") == Decimal("1000.00")
        assert result.employee_deductions == Decimal("120000.00")
        assert result.employer_contributions == Decimal("120000.00")
        assert result.net == Decimal("880000.00")

    def test_a_full_month(self) -> None:
        """26 of 26 days: gross is the whole salary, net is 88% of it."""
        result = compute_payslip(
            spans=[span("2000000", "26")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
        )
        assert result.gross == Decimal("2000000.00")
        assert result.employee_deductions == Decimal("240000.00")
        assert result.net == Decimal("1760000.00")

    def test_no_days_worked_earns_nothing_and_is_not_an_error(self) -> None:
        result = compute_payslip(
            spans=[span("2000000", "0")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
        )
        assert result.gross == Decimal("0.00")
        assert result.employee_deductions == Decimal("0.00")
        assert result.employer_contributions == Decimal("0.00")
        assert result.net == Decimal("0.00")

    def test_a_zero_denominator_is_refused_rather_than_divided_by(self) -> None:
        """The spreadsheet never met this because the divisor was typed."""
        with pytest.raises(ValidationFailed) as refusal:
            compute_payslip(
                spans=[span("2000000", "0")],
                working_days=0,
                rules=current_rules(),
                on_date=MARCH,
            )
        assert "at least one working day" in str(refusal.value)

    def test_more_days_worked_than_the_month_has_is_refused(self) -> None:
        with pytest.raises(ValidationFailed):
            compute_payslip(
                spans=[span("2000000", "27")],
                working_days=26,
                rules=current_rules(),
                on_date=MARCH,
            )

    def test_an_odd_number_of_days_rounds_to_the_som(self) -> None:
        """1,000,000 x 17 / 22 = 772,727.2727..., quantized to 772,727.27."""
        result = compute_payslip(
            spans=[span("1000000", "17")],
            working_days=22,
            rules=current_rules(),
            on_date=MARCH,
        )
        assert result.gross == Decimal("772727.27")
        # 12% of 772,727.27 = 92,727.2724 -> 92,727.27; INPS 0.1% = 772.73.
        assert amount_of(result, "INPS") == Decimal("772.73")
        assert amount_of(result, "INCOME_TAX") == Decimal("91954.54")
        assert result.employee_deductions == Decimal("92727.27")
        assert result.net == Decimal("680000.00")


class TestTheEmployerNeverReducesNetPay:
    def test_social_tax_is_a_contribution_not_a_deduction(self) -> None:
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
        )
        categories = {line.code: line.category for line in result.lines}
        assert categories["SOCIAL_TAX"] is PayslipLineCategory.EMPLOYER_CONTRIBUTION
        assert categories["INCOME_TAX"] is PayslipLineCategory.EMPLOYEE_DEDUCTION
        # Net follows from gross and the employee's own deductions only.
        assert result.net == result.gross - result.employee_deductions

    def test_raising_the_social_tax_does_not_change_anybody_s_pay(self) -> None:
        low = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=[
                rule(StatutoryRuleKind.INCOME_TAX, "12"),
                rule(StatutoryRuleKind.INPS, "0.1", component_of=StatutoryRuleKind.INCOME_TAX),
                rule(StatutoryRuleKind.SOCIAL_TAX, "12"),
            ],
            on_date=MARCH,
        )
        high = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=[
                rule(StatutoryRuleKind.INCOME_TAX, "12"),
                rule(StatutoryRuleKind.INPS, "0.1", component_of=StatutoryRuleKind.INCOME_TAX),
                rule(StatutoryRuleKind.SOCIAL_TAX, "25"),
            ],
            on_date=MARCH,
        )
        assert low.net == high.net == Decimal("880000.00")
        assert high.employer_contributions == Decimal("250000.00")


class TestTheComponentRule:
    def test_inps_comes_out_of_the_income_tax_not_on_top_of_it(self) -> None:
        """The defect the audit found, stated as a test.

        The workbook charged one employee 12% income tax *and* 0.1% INPS
        through 2019, withholding 12.1%. The 0.1 percentage point is part of
        the 12%, so the total must be exactly 12%.
        """
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
        )
        assert amount_of(result, "INCOME_TAX") + amount_of(result, "INPS") == Decimal(
            "120000.00"
        )
        assert result.employee_deductions == Decimal("120000.00")

    def test_a_component_without_its_parent_is_refused(self) -> None:
        with pytest.raises(ValidationFailed) as refusal:
            compute_payslip(
                spans=[span("1000000", "26")],
                working_days=26,
                rules=[
                    rule(
                        StatutoryRuleKind.INPS, "0.1", component_of=StatutoryRuleKind.INCOME_TAX
                    )
                ],
                on_date=MARCH,
            )
        assert "no approved rule" in str(refusal.value)

    def test_a_component_larger_than_its_parent_is_refused(self) -> None:
        with pytest.raises(ValidationFailed):
            compute_payslip(
                spans=[span("1000000", "26")],
                working_days=26,
                rules=[
                    rule(StatutoryRuleKind.INCOME_TAX, "1"),
                    rule(
                        StatutoryRuleKind.INPS, "5", component_of=StatutoryRuleKind.INCOME_TAX
                    ),
                ],
                on_date=MARCH,
            )


class TestRulesChangeWithTheLaw:
    def test_a_2018_period_uses_the_2018_rules(self) -> None:
        """8% employee pension, no INPS: the law as it was.

        Gross 1,000,000; the 8% contribution is 80,000; net 920,000. There
        is no income tax line because the flat rate did not exist yet and
        2018's progressive scale is not reproduced (audit question P10).
        """
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=[
                rule(
                    StatutoryRuleKind.EMPLOYEE_PENSION,
                    "8",
                    effective_from=date(2018, 1, 1),
                    effective_to=date(2018, 12, 31),
                ),
                rule(StatutoryRuleKind.SOCIAL_TAX, "15", effective_from=date(2018, 1, 1),
                     effective_to=date(2018, 12, 31)),
            ],
            on_date=date(2018, 6, 30),
        )
        assert amount_of(result, "EMPLOYEE_PENSION") == Decimal("80000.00")
        assert result.net == Decimal("920000.00")
        assert result.employer_contributions == Decimal("150000.00")

    def test_the_same_month_in_2019_uses_the_2019_rules(self) -> None:
        """The 8% is gone and the 12% has arrived: net 880,000, not 920,000."""
        rules = [
            rule(
                StatutoryRuleKind.EMPLOYEE_PENSION,
                "8",
                effective_from=date(2018, 1, 1),
                effective_to=date(2018, 12, 31),
            ),
            *current_rules(),
        ]
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=rules,
            on_date=date(2019, 6, 30),
        )
        assert not any(line.code == "EMPLOYEE_PENSION" for line in result.lines)
        assert result.net == Decimal("880000.00")

    def test_a_rule_approved_later_does_not_reach_an_earlier_period(self) -> None:
        later = rule(StatutoryRuleKind.SOCIAL_TAX, "25", effective_from=date(2027, 1, 1))
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=[*current_rules(), later],
            on_date=MARCH,
        )
        assert result.employer_contributions == Decimal("120000.00")


class TestSalaryChangesMidPeriod:
    def test_each_span_is_paid_at_its_own_salary(self) -> None:
        """10 days at 1,000,000 and 12 at 1,500,000, over 22 working days.

        By hand: 1,000,000 x 10 / 22 = 454,545.4545... -> 454,545.45, and
        1,500,000 x 12 / 22 = 818,181.8181... -> 818,181.82. Gross is their
        sum, 1,272,727.27.
        """
        result = compute_payslip(
            spans=[span("1000000", "10"), span("1500000", "12")],
            working_days=22,
            rules=current_rules(),
            on_date=MARCH,
        )
        earnings = [
            line.amount for line in result.lines if line.category is PayslipLineCategory.EARNING
        ]
        assert earnings == [Decimal("454545.45"), Decimal("818181.82")]
        assert result.gross == Decimal("1272727.27")
        assert result.worked_days == Decimal("22")


class TestBonusesAndDeductions:
    def test_a_bonus_is_taxed_like_the_rest_of_the_pay(self) -> None:
        """Gross 1,000,000 + 200,000 bonus = 1,200,000; 12% = 144,000."""
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
            adjustments=[
                AdjustmentInput(
                    id=uuid.uuid4(),
                    kind=AdjustmentKind.BONUS,
                    amount=Decimal("200000"),
                    reason="Quarterly bonus agreed by the owner",
                )
            ],
        )
        assert result.gross == Decimal("1200000.00")
        assert result.employee_deductions == Decimal("144000.00")
        assert result.net == Decimal("1056000.00")

    def test_an_agreed_deduction_reduces_net_but_not_the_tax(self) -> None:
        """A deduction is taken after tax: 880,000 - 50,000 = 830,000."""
        result = compute_payslip(
            spans=[span("1000000", "26")],
            working_days=26,
            rules=current_rules(),
            on_date=MARCH,
            adjustments=[
                AdjustmentInput(
                    id=uuid.uuid4(),
                    kind=AdjustmentKind.DEDUCTION,
                    amount=Decimal("50000"),
                    reason="Repayment of a salary advance, agreed in writing",
                )
            ],
        )
        assert amount_of(result, "INCOME_TAX") == Decimal("119000.00")
        assert result.net == Decimal("830000.00")

    def test_deductions_larger_than_the_pay_are_refused(self) -> None:
        with pytest.raises(ValidationFailed):
            compute_payslip(
                spans=[span("100000", "26")],
                working_days=26,
                rules=current_rules(),
                on_date=MARCH,
                adjustments=[
                    AdjustmentInput(
                        id=uuid.uuid4(),
                        kind=AdjustmentKind.DEDUCTION,
                        amount=Decimal("500000"),
                        reason="An impossible deduction",
                    )
                ],
            )


class TestWhatBlocksAPeriod:
    def test_a_period_with_no_approved_rules_names_them_all(self) -> None:
        missing = missing_rule_kinds(rules=[], on_date=MARCH)
        assert StatutoryRuleKind.INCOME_TAX in missing
        assert StatutoryRuleKind.SOCIAL_TAX in missing

    def test_an_approved_income_tax_leaves_only_the_social_tax(self) -> None:
        """Which is the state the clinic is actually in: P2 is unanswered."""
        missing = missing_rule_kinds(
            rules=[rule(StatutoryRuleKind.INCOME_TAX, "12")], on_date=MARCH
        )
        assert missing == [StatutoryRuleKind.SOCIAL_TAX]

    def test_nothing_is_missing_once_both_are_approved(self) -> None:
        assert missing_rule_kinds(rules=current_rules(), on_date=MARCH) == []

    def test_a_rule_that_expired_before_the_period_does_not_count(self) -> None:
        expired = rule(
            StatutoryRuleKind.INCOME_TAX,
            "12",
            effective_from=date(2019, 1, 1),
            effective_to=date(2020, 12, 31),
        )
        missing = missing_rule_kinds(rules=[expired], on_date=MARCH)
        assert StatutoryRuleKind.INCOME_TAX in missing
