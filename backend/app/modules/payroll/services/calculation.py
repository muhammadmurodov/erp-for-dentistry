"""Turning a salary, a timesheet and a set of approved rules into a payslip.

This file is deliberately pure: it takes values in and returns a result,
touches no session, and reads no clock. Everything about a payslip can
therefore be checked by hand against the same numbers, which is what the
tests do — a figure is compared with one worked out independently, not with
whatever the code happened to produce last time.

**Three rules hold here and are each worth stating.**

*Proration is `worked / working * salary`*, the clinic's own formula,
unchanged in its workbook since 2018 and the one safe calculation the audit
found. A zero denominator is not a division: it is a refusal, because a
month with no working days is a question about the calendar, not a payslip.

*The employee's deductions and the employer's contributions never mix.*
They are separate lists with separate totals, and net pay is computed from
one of them only. The brief asks for that; the database states it as well
(`ck_payslip_net_identity`); and this module makes it structural by keeping
the two in different fields of the result.

*A component is not an extra deduction.* INPS is 0.1 percentage points **of**
the 12% income tax, not 0.1% on top of it — the clinic's own posting rule
moves it from the income-tax payable to the pension account. A rule marked
`component_of` therefore reduces the rule it belongs to rather than adding
to what is withheld. The workbook got this wrong for one employee for a
year, charging 12% beside a separate 0.1% (`WORKBOOK_FORMULA_AUDIT.md`,
defect D2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.errors import ValidationFailed
from app.core.money import ZERO, quantize
from app.modules.payroll.enums import (
    EMPLOYEE_KINDS,
    EMPLOYER_KINDS,
    AdjustmentKind,
    PayslipLineCategory,
    RuleMethod,
    StatutoryRuleKind,
)

HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class RuleInput:
    """An approved statutory rule, as the engine needs it."""

    id: uuid.UUID
    kind: StatutoryRuleKind
    method: RuleMethod
    rate: Decimal | None
    fixed_amount: Decimal | None
    component_of: StatutoryRuleKind | None
    effective_from: date
    effective_to: date | None

    def applies_on(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


@dataclass(frozen=True, slots=True)
class AdjustmentInput:
    id: uuid.UUID
    kind: AdjustmentKind
    amount: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class SalarySpan:
    """A salary and the days of the period it was agreed for.

    A pay rise mid-month produces two spans. Each carries the days actually
    worked inside it, counted from the timesheet, so nothing has to be
    assumed about how the worked days were distributed.
    """

    compensation_id: uuid.UUID
    monthly_salary: Decimal
    worked_days: Decimal


@dataclass(frozen=True, slots=True)
class ComputedLine:
    category: PayslipLineCategory
    code: str
    description: str
    amount: Decimal
    rate: Decimal | None = None
    statutory_rule_id: uuid.UUID | None = None
    compensation_id: uuid.UUID | None = None
    adjustment_id: uuid.UUID | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ComputedPayslip:
    worked_days: Decimal
    working_days: int
    monthly_salary: Decimal
    gross: Decimal
    employee_deductions: Decimal
    employer_contributions: Decimal
    net: Decimal
    lines: list[ComputedLine] = field(default_factory=list)


def compute_payslip(
    *,
    spans: list[SalarySpan],
    working_days: int,
    rules: list[RuleInput],
    on_date: date,
    adjustments: list[AdjustmentInput] | None = None,
    currency: str = "UZS",
) -> ComputedPayslip:
    """One payslip, from one month's facts and the rules in force.

    `on_date` is the day the rules are read for — the last day of the period
    — so a run for March 2019 uses March 2019's law however long afterwards
    it is computed.
    """
    if working_days <= 0:
        # The workbook never hit this because its denominator was typed by
        # hand every month. A system that divides anyway produces an
        # infinite salary, so this is a refusal rather than a guard.
        raise ValidationFailed(
            "A payroll period must have at least one working day, so the "
            "proration has something to divide by",
            details={"working_days": working_days},
        )
    if not spans:
        raise ValidationFailed("A payslip needs an agreed salary to compute from")

    adjustments = adjustments or []
    lines: list[ComputedLine] = []

    # --- earnings ---------------------------------------------------------
    gross = ZERO
    worked_days = ZERO
    for span in spans:
        if span.worked_days < ZERO:
            raise ValidationFailed("Days worked cannot be negative")
        earned = quantize(span.monthly_salary * span.worked_days / working_days, currency)
        gross += earned
        worked_days += span.worked_days
        lines.append(
            ComputedLine(
                category=PayslipLineCategory.EARNING,
                code="SALARY",
                description="Salary for days worked",
                amount=earned,
                compensation_id=span.compensation_id,
                note=f"{span.worked_days} of {working_days} days at {span.monthly_salary}",
            )
        )

    if worked_days > working_days:
        raise ValidationFailed(
            "More days were worked than the period has working days",
            details={"worked_days": str(worked_days), "working_days": working_days},
        )

    for adjustment in adjustments:
        if adjustment.kind is not AdjustmentKind.BONUS:
            continue
        gross += adjustment.amount
        lines.append(
            ComputedLine(
                category=PayslipLineCategory.EARNING,
                code="BONUS",
                description="Bonus",
                amount=adjustment.amount,
                adjustment_id=adjustment.id,
                note=adjustment.reason,
            )
        )
    gross = quantize(gross, currency)

    # --- statutory amounts ------------------------------------------------
    in_force = [rule for rule in rules if rule.applies_on(on_date)]
    amounts: dict[StatutoryRuleKind, Decimal] = {}
    rate_used: dict[StatutoryRuleKind, Decimal | None] = {}
    rule_used: dict[StatutoryRuleKind, uuid.UUID] = {}

    for rule in in_force:
        if rule.method is RuleMethod.PERCENT_OF_GROSS:
            assert rule.rate is not None
            amount = quantize(gross * rule.rate / HUNDRED, currency)
            rate_used[rule.kind] = rule.rate
        else:
            assert rule.fixed_amount is not None
            amount = quantize(rule.fixed_amount, currency)
            rate_used[rule.kind] = None
        amounts[rule.kind] = amount
        rule_used[rule.kind] = rule.id

    # A component comes *out of* the rule it belongs to. Both lines are
    # shown, because the employee is entitled to see where the 0.1 went,
    # but only the parent's full amount leaves their pay.
    components = {rule.kind: rule.component_of for rule in in_force if rule.component_of}
    for kind, parent in components.items():
        if parent not in amounts:
            raise ValidationFailed(
                f"{kind.value} is defined as a component of {parent.value}, which has "
                "no approved rule for this period",
                details={"kind": kind.value, "component_of": parent.value},
            )
        amounts[parent] = amounts[parent] - amounts[kind]
        if amounts[parent] < ZERO:
            raise ValidationFailed(
                f"{kind.value} is larger than the {parent.value} it is part of",
                details={"kind": kind.value, "component_of": parent.value},
            )

    employee_total = ZERO
    employer_total = ZERO
    for kind in sorted(amounts, key=lambda k: k.value):
        amount = amounts[kind]
        if amount == ZERO and kind not in EMPLOYEE_KINDS | EMPLOYER_KINDS:
            continue
        category = (
            PayslipLineCategory.EMPLOYEE_DEDUCTION
            if kind in EMPLOYEE_KINDS
            else PayslipLineCategory.EMPLOYER_CONTRIBUTION
        )
        if category is PayslipLineCategory.EMPLOYEE_DEDUCTION:
            employee_total += amount
        else:
            employer_total += amount
        lines.append(
            ComputedLine(
                category=category,
                code=kind.value,
                description=_describe(kind),
                amount=amount,
                rate=rate_used.get(kind),
                statutory_rule_id=rule_used[kind],
                note=(
                    f"part of {components[kind].value}"
                    if kind in components
                    else None
                ),
            )
        )

    # --- agreed deductions ------------------------------------------------
    for adjustment in adjustments:
        if adjustment.kind is not AdjustmentKind.DEDUCTION:
            continue
        employee_total += adjustment.amount
        lines.append(
            ComputedLine(
                category=PayslipLineCategory.EMPLOYEE_DEDUCTION,
                code="DEDUCTION",
                description="Agreed deduction",
                amount=adjustment.amount,
                adjustment_id=adjustment.id,
                note=adjustment.reason,
            )
        )

    employee_total = quantize(employee_total, currency)
    employer_total = quantize(employer_total, currency)
    net = quantize(gross - employee_total, currency)
    if net < ZERO:
        raise ValidationFailed(
            "Deductions come to more than the pay they are taken from",
            details={"gross": str(gross), "deductions": str(employee_total)},
        )

    return ComputedPayslip(
        worked_days=worked_days,
        working_days=working_days,
        monthly_salary=spans[-1].monthly_salary,
        gross=gross,
        employee_deductions=employee_total,
        employer_contributions=employer_total,
        net=net,
        lines=lines,
    )


def _describe(kind: StatutoryRuleKind) -> str:
    return {
        StatutoryRuleKind.INCOME_TAX: "Income tax withheld",
        StatutoryRuleKind.INPS: "Individual pension account (part of income tax)",
        StatutoryRuleKind.EMPLOYEE_PENSION: "Employee pension contribution",
        StatutoryRuleKind.SOCIAL_TAX: "Social tax (employer)",
    }[kind]


#: The statutory kinds a run must have an approved rule for before it may be
#: approved or posted. `INPS` is not here: it is a component of the income
#: tax and a period may legitimately predate it.
REQUIRED_KINDS: tuple[StatutoryRuleKind, ...] = (
    StatutoryRuleKind.INCOME_TAX,
    StatutoryRuleKind.SOCIAL_TAX,
)

#: What to tell somebody when a required rule has no approved version. The
#: question is named so they know who has to answer it rather than guessing.
BLOCKING_QUESTION: dict[StatutoryRuleKind, str] = {
    StatutoryRuleKind.INCOME_TAX: (
        "No approved income tax rule covers this period. The rate verified for "
        "2019-01-01 onward is 12% of gross, of which 0.1 percentage points is "
        "INPS; it is seeded as a draft and an accountant must approve it."
    ),
    StatutoryRuleKind.SOCIAL_TAX: (
        "No approved social tax rule covers this period. The current rate could "
        "not be confirmed from an authoritative source during the workbook audit "
        "— see OPEN_QUESTIONS P2 — so nothing is seeded and an accountant must "
        "enter and approve the rate that applies."
    ),
    StatutoryRuleKind.EMPLOYEE_PENSION: (
        "No approved employee pension rule covers this period. The 8% "
        "contribution was abolished on 2019-01-01."
    ),
    StatutoryRuleKind.INPS: (
        "No approved INPS rule covers this period."
    ),
}


def missing_rule_kinds(
    *, rules: list[RuleInput], on_date: date
) -> list[StatutoryRuleKind]:
    """Which required rules have no approved version in force on this day."""
    in_force = {rule.kind for rule in rules if rule.applies_on(on_date)}
    return [kind for kind in REQUIRED_KINDS if kind not in in_force]
