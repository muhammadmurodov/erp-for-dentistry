"""Payroll: versioned rules, and the runs computed under them.

**A payslip stores its own arithmetic.** Every line records the amount, the
rate that produced it and the identifier of the rule version that supplied
that rate. Nothing about an approved run is ever recomputed, so a rule
approved next year cannot reach back and change what somebody was paid last
year — the figures do not depend on the rules table once they are approved,
only on the row in front of you.

That is the requirement the workbook could not meet. Its payroll sheet
recomputes every historical month from today's formulas, which is why
switching one cell to `*0` silently rewrote three months of 2023, and why
the 2018 figures vanished the day the macro went missing
(`WORKBOOK_FORMULA_AUDIT.md`, defects D1 and P6).

**A statutory rate is never a literal in code.** It is a row with an
effective date, a legal reference and an approver. Until somebody with
authority approves it, it calculates nothing.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.payroll.enums import (
    AdjustmentKind,
    CompensationKind,
    PayrollRunStatus,
    PayslipLineCategory,
    RuleMethod,
    RuleStatus,
    StatutoryRuleKind,
)

#: A rate is a percentage with four decimals: 11.9% is 11.9000, and the
#: 0.1 percentage point that goes to INPS has to survive the arithmetic.
RateType = MoneyType


def _enum(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _org_scope(table: str) -> tuple:
    return (UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id"),)


def _org_column() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def _parent_fk(table: str, column: str, parent: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{parent}.organization_id", f"{parent}.id"],
        ondelete="RESTRICT",
        name=name,
    )


class StatutoryRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One statutory rate, for one period, with the reference that proves it.

    **The reference is required for approval, not for drafting.** Somebody
    may write down what they believe the rate is; nobody may approve it
    without saying where it comes from and when it took effect. That is the
    difference between this table and a constant in a spreadsheet.
    """

    __tablename__ = "payroll_statutory_rule"

    organization_id: Mapped[uuid.UUID] = _org_column()
    kind: Mapped[StatutoryRuleKind] = mapped_column(
        _enum(StatutoryRuleKind, "payroll_statutory_rule_kind"), nullable=False
    )
    method: Mapped[RuleMethod] = mapped_column(
        _enum(RuleMethod, "payroll_rule_method"), nullable=False
    )
    #: Per cent, when the method is a percentage of gross.
    rate: Mapped[Decimal | None] = mapped_column(RateType)
    #: A sum per period, when the method is a fixed amount.
    fixed_amount: Mapped[Decimal | None] = mapped_column(MoneyType)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: Null while the rule is still in force. Set when a later one
    #: supersedes it, or when the law withdrew it — the 8% employee pension
    #: contribution ends on 2018-12-31 and is not deleted, because runs from
    #: 2018 are still read.
    effective_to: Mapped[date | None] = mapped_column(Date)

    #: Another kind whose amount this one comes out of, rather than adding
    #: to the employee's deductions. INPS is a component of the income tax:
    #: the clinic's own posting rule moves it from 6411 to 6520.02.
    component_of: Mapped[StatutoryRuleKind | None] = mapped_column(
        _enum(StatutoryRuleKind, "payroll_component_of_kind")
    )

    #: Which law, decree or article. Required before approval.
    legal_reference: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    #: Where applicability is narrower than "everybody from this date".
    applicability: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[RuleStatus] = mapped_column(
        _enum(RuleStatus, "payroll_rule_status"), nullable=False, default=RuleStatus.DRAFT
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        *_org_scope("payroll_statutory_rule"),
        CheckConstraint(
            "(method = 'PERCENT_OF_GROSS' AND rate IS NOT NULL AND fixed_amount IS NULL) "
            "OR (method = 'FIXED_AMOUNT' AND fixed_amount IS NOT NULL AND rate IS NULL)",
            name="ck_payroll_rule_method_has_its_value",
        ),
        CheckConstraint("rate IS NULL OR rate >= 0", name="ck_payroll_rule_rate_not_negative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_payroll_rule_period_ordered",
        ),
        # An approved rule names its authority and says who approved it.
        # Drafting is free; approving is not.
        CheckConstraint(
            "status <> 'APPROVED' OR (approved_at IS NOT NULL AND legal_reference IS NOT NULL)",
            name="ck_payroll_rule_approved_has_reference",
        ),
        Index("ix_payroll_rule_kind_period", "organization_id", "kind", "effective_from"),
    )


class EmployeeCompensation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What the clinic agreed to pay one person, from one date.

    A pay rise is a new row, not an edit: the old row keeps its dates and
    the payslips that cited it stay explicable. Overlapping live rows for
    one employee are refused by a partial unique index on the open-ended
    one, and by the service for the rest.
    """

    __tablename__ = "employee_compensation"

    organization_id: Mapped[uuid.UUID] = _org_column()
    employee_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[CompensationKind] = mapped_column(
        _enum(CompensationKind, "employee_compensation_kind"),
        nullable=False,
        default=CompensationKind.FIXED_MONTHLY,
    )
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        *_org_scope("employee_compensation"),
        _parent_fk("employee_compensation", "employee_id", "employee", "fk_compensation_employee"),
        CheckConstraint("amount >= 0", name="ck_compensation_amount_not_negative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_compensation_period_ordered",
        ),
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "effective_from",
            name="uq_compensation_employee_from",
        ),
        Index("ix_compensation_employee", "organization_id", "employee_id", "effective_from"),
    )


class PayrollRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One month's payroll, and the four separate acts it goes through.

    `working_days` is stated on the run rather than derived, because the
    clinic's own timesheet is the authority for how many working days a
    month had, and a calendar in code would disagree with it on the first
    public holiday.
    """

    __tablename__ = "payroll_run"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    working_days: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UZS")

    status: Mapped[PayrollRunStatus] = mapped_column(
        _enum(PayrollRunStatus, "payroll_run_status"),
        nullable=False,
        default=PayrollRunStatus.DRAFT,
    )
    #: Why this run may not be approved or posted: the statutory rules it
    #: has no approved version of, named so somebody can act. Recomputed on
    #: every calculation and re-checked before approval and before posting.
    blocked_reasons: Mapped[str | None] = mapped_column(Text)

    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calculated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    #: The accounting entry this run produced. One entry per run, written
    #: once: the uniqueness is what stops a run being posted twice.
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("payroll_run"),
        UniqueConstraint("organization_id", "code", name="uq_payroll_run_code"),
        # One run per month. A correction is a new run for a later month or
        # an authorised adjustment, never a second run for the same one.
        UniqueConstraint("organization_id", "year", "month", name="uq_payroll_run_period"),
        UniqueConstraint(
            "organization_id", "journal_entry_id", name="uq_payroll_run_journal_entry"
        ),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_payroll_run_month"),
        CheckConstraint("working_days > 0", name="ck_payroll_run_working_days_positive"),
        CheckConstraint("period_end >= period_start", name="ck_payroll_run_period_ordered"),
        CheckConstraint(
            "(status IN ('POSTED', 'PAID')) = (journal_entry_id IS NOT NULL)",
            name="ck_payroll_run_posted_has_entry",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancelled_reason IS NOT NULL",
            name="ck_payroll_run_cancelled_has_reason",
        ),
        Index("ix_payroll_run_status", "organization_id", "status"),
    )


class Payslip(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person, one month: the totals, kept as computed.

    Every total here is also the sum of this payslip's lines, and a test
    asserts it. Storing both is not a second answer — the lines are the
    working and the totals are what was agreed — but they must agree, and
    the only way they can diverge is a bug, so it is checked.
    """

    __tablename__ = "payslip"

    organization_id: Mapped[uuid.UUID] = _org_column()
    run_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    worked_days: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    working_days: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The agreed monthly salary this slip was computed from, copied here so
    #: the slip explains itself without reading the compensation table.
    monthly_salary: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)

    gross: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    employee_deductions: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=0)
    employer_contributions: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=0)
    net: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)

    __table_args__ = (
        *_org_scope("payslip"),
        _parent_fk("payslip", "run_id", "payroll_run", "fk_payslip_run"),
        _parent_fk("payslip", "employee_id", "employee", "fk_payslip_employee"),
        UniqueConstraint("organization_id", "run_id", "employee_id", name="uq_payslip_once"),
        CheckConstraint("worked_days >= 0", name="ck_payslip_worked_days_not_negative"),
        CheckConstraint("worked_days <= working_days", name="ck_payslip_worked_within_working"),
        CheckConstraint("working_days > 0", name="ck_payslip_working_days_positive"),
        CheckConstraint("gross >= 0", name="ck_payslip_gross_not_negative"),
        # Net pay is gross less the employee's own deductions. The employer's
        # contributions are not in this identity, and the database is where
        # that is stated so no service can quietly change it.
        CheckConstraint("net = gross - employee_deductions", name="ck_payslip_net_identity"),
        Index("ix_payslip_run", "organization_id", "run_id"),
    )


class PayslipLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One figure on a payslip, and the rule version that produced it.

    `statutory_rule_id` is what makes an approved run reproducible: the
    amount is already stored, and the rule it came from can still be read
    years later even after a newer rule supersedes it.
    """

    __tablename__ = "payslip_line"

    organization_id: Mapped[uuid.UUID] = _org_column()
    payslip_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    category: Mapped[PayslipLineCategory] = mapped_column(
        _enum(PayslipLineCategory, "payslip_line_category"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    #: The rate applied, kept beside the amount so a payslip can be checked
    #: by hand without opening the rules table.
    rate: Mapped[Decimal | None] = mapped_column(RateType)
    statutory_rule_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    compensation_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    adjustment_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("payslip_line"),
        _parent_fk("payslip_line", "payslip_id", "payslip", "fk_payslip_line_payslip"),
        _parent_fk(
            "payslip_line",
            "statutory_rule_id",
            "payroll_statutory_rule",
            "fk_payslip_line_rule",
        ),
        CheckConstraint("amount >= 0", name="ck_payslip_line_amount_not_negative"),
        Index("ix_payslip_line_payslip", "organization_id", "payslip_id"),
    )


class PayrollAdjustment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A bonus or a deduction, for one person in one month, with a reason.

    The reason is `NOT NULL` and the authoriser is recorded, because the
    workbook's nine undocumented corrections are exactly what this table
    exists to prevent (`WORKBOOK_FORMULA_AUDIT.md`, question P8). An
    adjustment nobody can explain cannot be entered here at all.
    """

    __tablename__ = "payroll_adjustment"

    organization_id: Mapped[uuid.UUID] = _org_column()
    run_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[AdjustmentKind] = mapped_column(
        _enum(AdjustmentKind, "payroll_adjustment_kind"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    authorised_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("payroll_adjustment"),
        _parent_fk("payroll_adjustment", "run_id", "payroll_run", "fk_adjustment_run"),
        _parent_fk("payroll_adjustment", "employee_id", "employee", "fk_adjustment_employee"),
        CheckConstraint("amount > 0", name="ck_payroll_adjustment_amount_positive"),
        CheckConstraint("char_length(btrim(reason)) > 0", name="ck_payroll_adjustment_reason"),
        Index("ix_payroll_adjustment_run", "organization_id", "run_id"),
    )
