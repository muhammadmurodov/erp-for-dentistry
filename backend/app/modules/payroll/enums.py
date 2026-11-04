"""What a payroll rule is, and what a run has been through.

Two kinds of rule live in this module and they are deliberately not the same
table. A **statutory rule** is the state's: an income tax rate, a pension
contribution, a social tax. Nobody at the clinic decides it, it changes when
the law changes, and it must carry the reference that proves it. A
**compensation** is the clinic's own agreement with one person: a monthly
salary, effective from a date.

Confusing the two is how a spreadsheet ends up with a tax rate typed into
the same cell as somebody's wage.
"""

from __future__ import annotations

from enum import StrEnum


class StatutoryRuleKind(StrEnum):
    """A deduction or contribution the state requires.

    Each is modelled separately because each lands in a different account
    and some are the employee's while others are the employer's — the
    distinction that decides whether a figure may reduce net pay.
    """

    INCOME_TAX = "INCOME_TAX"
    """Withheld from the employee. Uzbek ЖШДС / НДФЛ."""

    INPS = "INPS"
    """The individual savings pension account. **A component of the income
    tax, not an addition to it** — the clinic's own posting rule takes it out
    of the income tax payable (Dr 6411 / Cr 6520.02), which is what
    `component_of` records."""

    EMPLOYEE_PENSION = "EMPLOYEE_PENSION"
    """Withheld from the employee for the state pension fund. Uzbek СВГПФ.
    Abolished from 2019-01-01; kept because historical runs need it."""

    SOCIAL_TAX = "SOCIAL_TAX"
    """Paid by the **employer** on top of the wage. Uzbek ижтимоий солиқ /
    ЕСП. Never reduces net pay, and the type system makes that hard to get
    wrong: it is the only kind whose category is a contribution."""


#: The kinds the employee pays, out of their own gross.
EMPLOYEE_KINDS: frozenset[StatutoryRuleKind] = frozenset(
    {
        StatutoryRuleKind.INCOME_TAX,
        StatutoryRuleKind.INPS,
        StatutoryRuleKind.EMPLOYEE_PENSION,
    }
)

#: The kinds the employer pays, beside the wage and never out of it.
EMPLOYER_KINDS: frozenset[StatutoryRuleKind] = frozenset({StatutoryRuleKind.SOCIAL_TAX})


class RuleMethod(StrEnum):
    """How a rule turns a wage into an amount."""

    PERCENT_OF_GROSS = "PERCENT_OF_GROSS"
    """`rate` per cent of the period's gross pay."""

    FIXED_AMOUNT = "FIXED_AMOUNT"
    """`fixed_amount` per period, whatever the wage."""


class RuleStatus(StrEnum):
    """A rule is proposed, then approved by somebody with authority.

    A `DRAFT` rule calculates nothing and posts nothing. This is the whole
    point: a rate nobody has approved is a rate nobody has checked, and the
    system would rather refuse than guess.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class CompensationKind(StrEnum):
    """What the clinic agreed to pay somebody."""

    FIXED_MONTHLY = "FIXED_MONTHLY"
    """A monthly salary, prorated by days actually worked."""


class PayrollRunStatus(StrEnum):
    """Calculating, approving, posting and paying are four separate acts.

    They are four states rather than flags because the clinic's brief is
    explicit that approval, the accounting entry and the disbursement are
    distinct events — and because each is done by a different permission.
    """

    DRAFT = "DRAFT"
    CALCULATED = "CALCULATED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


#: A run nobody has approved yet, which may still be recalculated.
OPEN_RUN_STATUSES: frozenset[PayrollRunStatus] = frozenset(
    {PayrollRunStatus.DRAFT, PayrollRunStatus.CALCULATED}
)

#: Once a run is here, its payslips are history and are never recomputed.
SETTLED_RUN_STATUSES: frozenset[PayrollRunStatus] = frozenset(
    {PayrollRunStatus.APPROVED, PayrollRunStatus.POSTED, PayrollRunStatus.PAID}
)


class PayslipLineCategory(StrEnum):
    """Which side of the payslip a line belongs to.

    The separation this enum enforces is the one the brief insists on: an
    `EMPLOYER_CONTRIBUTION` is a cost of employing somebody and **never**
    reduces what they are paid.
    """

    EARNING = "EARNING"
    EMPLOYEE_DEDUCTION = "EMPLOYEE_DEDUCTION"
    EMPLOYER_CONTRIBUTION = "EMPLOYER_CONTRIBUTION"


class AdjustmentKind(StrEnum):
    """A one-off addition or subtraction, agreed for one person, one month."""

    BONUS = "BONUS"
    DEDUCTION = "DEDUCTION"
