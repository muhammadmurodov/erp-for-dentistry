"""A payroll run: calculated, approved, posted and paid — four separate acts.

They are separate because they are separate decisions, made by different
people at different times, and conflating them is how a clinic pays somebody
twice or books a cost it has not incurred. Calculating works out what is
owed. Approving says the figures are right. Posting puts the expense and the
debt into the books. Paying settles that debt and touches no expense at all.

**Posting is blocked while any required statutory rule is unapproved**, and
the refusal names the rule and the open question behind it rather than
saying something went wrong. At the time of writing that means every run is
blocked on the social tax rate, because the workbook audit could not confirm
the current one (`OPEN_QUESTIONS.md` P2). That is the system working, not a
gap in it.

**An approved run is never recomputed.** Its payslips hold their own
amounts, rates and rule identifiers, so a rule approved afterwards changes
nothing that has already been agreed. Recalculating is only possible while
the run is still open.
"""

from __future__ import annotations

import calendar
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO, quantize
from app.modules.accounting.enums import PostingPurpose, SourceKind
from app.modules.accounting.services.numbering import next_number
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import (
    PostingLine,
    PostingRequest,
    PostingService,
)
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.hr.enums import AttendanceCode, EmploymentStatus
from app.modules.hr.services.attendance import AttendanceService
from app.modules.hr.services.employees import EmployeeService, display_name
from app.modules.payroll.enums import (
    OPEN_RUN_STATUSES,
    AdjustmentKind,
    PayrollRunStatus,
    PayslipLineCategory,
    StatutoryRuleKind,
)
from app.modules.payroll.models import (
    PayrollAdjustment,
    PayrollRun,
    Payslip,
    PayslipLine,
)
from app.modules.payroll.services.calculation import (
    BLOCKING_QUESTION,
    AdjustmentInput,
    SalarySpan,
    compute_payslip,
    missing_rule_kinds,
)
from app.modules.payroll.services.policy import CompensationService, StatutoryRuleService

OPERATION_WAGES = "payroll_wages_accrued"
OPERATION_INCOME_TAX = "payroll_income_tax_withheld"
OPERATION_INPS = "payroll_inps_withheld"
OPERATION_SOCIAL_TAX = "payroll_social_tax_accrued"
OPERATION_EMPLOYEE_PENSION = "payroll_employee_pension_withheld"
OPERATION_PAID_BANK = "payroll_paid_bank"
OPERATION_PAID_CASH = "payroll_paid_cash"

#: Which posting rule settles each statutory withholding.
WITHHOLDING_OPERATIONS: dict[StatutoryRuleKind, str] = {
    StatutoryRuleKind.INCOME_TAX: OPERATION_INCOME_TAX,
    StatutoryRuleKind.INPS: OPERATION_INPS,
    StatutoryRuleKind.EMPLOYEE_PENSION: OPERATION_EMPLOYEE_PENSION,
}


@dataclass(frozen=True, slots=True)
class PayslipView:
    payslip: Payslip
    lines: list[PayslipLine]


class PayrollRunService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._rules = StatutoryRuleService(session, audit)
        self._compensation = CompensationService(session, audit)
        self._employees = EmployeeService(session, audit)
        self._attendance = AttendanceService(session, audit)
        self._posting = PostingService(session, audit)

    # ------------------------------------------------------------- reading

    async def list_runs(
        self, *, organization_id: uuid.UUID, year: int | None = None
    ) -> list[PayrollRun]:
        stmt = select(PayrollRun).where(PayrollRun.organization_id == organization_id)
        if year is not None:
            stmt = stmt.where(PayrollRun.year == year)
        stmt = stmt.order_by(PayrollRun.year.desc(), PayrollRun.month.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, *, organization_id: uuid.UUID, run_id: uuid.UUID) -> PayrollRun:
        stmt = select(PayrollRun).where(
            PayrollRun.organization_id == organization_id, PayrollRun.id == run_id
        )
        run = (await self._session.execute(stmt)).scalar_one_or_none()
        if run is None:
            raise NotFound("Payroll run not found")
        return run

    async def payslips(
        self, *, organization_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[PayslipView]:
        stmt = (
            select(Payslip)
            .where(Payslip.organization_id == organization_id, Payslip.run_id == run_id)
            .order_by(Payslip.created_at)
        )
        slips = list((await self._session.execute(stmt)).scalars().all())
        out: list[PayslipView] = []
        for slip in slips:
            lines = list(
                (
                    await self._session.execute(
                        select(PayslipLine)
                        .where(
                            PayslipLine.organization_id == organization_id,
                            PayslipLine.payslip_id == slip.id,
                        )
                        .order_by(PayslipLine.category, PayslipLine.code)
                    )
                )
                .scalars()
                .all()
            )
            out.append(PayslipView(payslip=slip, lines=lines))
        return out

    async def blocking_reasons(
        self, *, organization_id: uuid.UUID, on_date: date
    ) -> list[str]:
        """Why this period cannot be posted, in words somebody can act on."""
        rules = await self._rules.rules_in_force(
            organization_id=organization_id, on_date=on_date
        )
        missing = missing_rule_kinds(rules=rules, on_date=on_date)
        return [BLOCKING_QUESTION[kind] for kind in missing]

    # ------------------------------------------------------------- the run

    async def open_run(
        self,
        *,
        organization_id: uuid.UUID,
        year: int,
        month: int,
        working_days: int,
        notes: str | None = None,
        currency: str = "UZS",
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollRun:
        if not 1 <= month <= 12:
            raise ValidationFailed("A month is between 1 and 12")
        if working_days <= 0:
            raise ValidationFailed(
                "State how many working days the month had. The clinic's "
                "timesheet is the authority for this, and a payroll cannot "
                "prorate against zero."
            )
        existing = await self._session.execute(
            select(PayrollRun).where(
                PayrollRun.organization_id == organization_id,
                PayrollRun.year == year,
                PayrollRun.month == month,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise Conflict(
                f"Payroll for {year}-{month:02d} already exists. A correction is "
                "an authorised adjustment or a later run, never a second run "
                "for the same month.",
                details={"year": year, "month": month},
            )

        last_day = calendar.monthrange(year, month)[1]
        run = PayrollRun(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="payroll_run",
                prefix="PR-",
                padding=5,
            ),
            year=year,
            month=month,
            period_start=date(year, month, 1),
            period_end=date(year, month, last_day),
            working_days=working_days,
            currency=currency,
            status=PayrollRunStatus.DRAFT,
            notes=(notes or "").strip() or None,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def calculate(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollRun:
        """Work out every payslip from the agreements and the rules in force.

        Recalculation replaces the previous payslips, which is safe only
        while the run is open — once approved, the figures are history.
        """
        run = await self.get(organization_id=organization_id, run_id=run_id)
        if run.status not in OPEN_RUN_STATUSES:
            raise Conflict(
                f"This run is {run.status.value.lower()} and its payslips are "
                "settled. Recalculating would rewrite figures somebody has "
                "already agreed.",
                details={"status": run.status.value},
            )

        await self._clear_payslips(organization_id=organization_id, run_id=run_id)

        rules = await self._rules.rules_in_force(
            organization_id=organization_id, on_date=run.period_end
        )
        adjustments = await self.adjustments(organization_id=organization_id, run_id=run_id)
        employees = await self._employees.list_employees(
            organization_id=organization_id, statuses=[EmploymentStatus.ACTIVE]
        )

        for employee in employees:
            # Somebody who had not started, or had left, is not on this run.
            if employee.hired_on > run.period_end:
                continue
            if employee.ended_on is not None and employee.ended_on < run.period_start:
                continue

            spans = await self._salary_spans(
                organization_id=organization_id, employee=employee, run=run
            )
            if not spans:
                continue

            computed = compute_payslip(
                spans=spans,
                working_days=run.working_days,
                rules=rules,
                on_date=run.period_end,
                adjustments=[
                    AdjustmentInput(
                        id=row.id, kind=row.kind, amount=row.amount, reason=row.reason
                    )
                    for row in adjustments
                    if row.employee_id == employee.id
                ],
                currency=run.currency,
            )

            slip = Payslip(
                organization_id=organization_id,
                run_id=run.id,
                employee_id=employee.id,
                worked_days=computed.worked_days,
                working_days=computed.working_days,
                monthly_salary=computed.monthly_salary,
                gross=computed.gross,
                employee_deductions=computed.employee_deductions,
                employer_contributions=computed.employer_contributions,
                net=computed.net,
            )
            self._session.add(slip)
            await self._session.flush()
            for line in computed.lines:
                self._session.add(
                    PayslipLine(
                        organization_id=organization_id,
                        payslip_id=slip.id,
                        category=line.category,
                        code=line.code,
                        description=line.description,
                        amount=line.amount,
                        rate=line.rate,
                        statutory_rule_id=line.statutory_rule_id,
                        compensation_id=line.compensation_id,
                        adjustment_id=line.adjustment_id,
                        note=line.note,
                    )
                )

        blocked = await self.blocking_reasons(
            organization_id=organization_id, on_date=run.period_end
        )
        run.blocked_reasons = json.dumps(blocked) if blocked else None
        run.status = PayrollRunStatus.CALCULATED
        run.calculated_at = datetime.now(UTC)
        run.calculated_by_user_id = actor_user_id
        await self._session.flush()

        self._audit.record(
            events.PAYROLL_CALCULATED,
            organization_id=organization_id,
            target_type="payroll_run",
            target_id=run.id,
            target_label=run.code,
            actor_user_id=actor_user_id,
            new_values={"blocked": blocked, "employees": len(employees)},
        )
        return run

    async def _salary_spans(
        self, *, organization_id: uuid.UUID, employee: object, run: PayrollRun
    ) -> list[SalarySpan]:
        """The agreements covering the period, each with its own days worked.

        When a salary changes mid-month the days are counted from the
        timesheet on each side of the change, so nothing is assumed about
        how the worked days fell.
        """
        employee_id = employee.id  # type: ignore[attr-defined]
        agreements = await self._compensation.covering(
            organization_id=organization_id,
            employee_id=employee_id,
            period_start=run.period_start,
            period_end=run.period_end,
        )
        if not agreements:
            return []

        days = await self._attendance.list_days(
            organization_id=organization_id,
            employee_id=employee_id,
            from_date=run.period_start,
            to_date=run.period_end,
        )
        worked = [day.on_date for day in days if day.code is AttendanceCode.WORKED]

        spans: list[SalarySpan] = []
        for agreement in agreements:
            start = max(agreement.effective_from, run.period_start)
            end = min(agreement.effective_to or run.period_end, run.period_end)
            in_span = Decimal(len([day for day in worked if start <= day <= end]))
            spans.append(
                SalarySpan(
                    compensation_id=agreement.id,
                    monthly_salary=agreement.amount,
                    worked_days=in_span,
                )
            )
        return spans

    async def _clear_payslips(self, *, organization_id: uuid.UUID, run_id: uuid.UUID) -> None:
        """Lines first, then the payslips, with a flush between them.

        The two deletes cannot be handed to one flush. A line's foreign key
        to its payslip is `RESTRICT` — deliberately, so no amount can lose
        the payslip it belongs to — and the models carry no relationship
        between them, so the unit of work has nothing to order them by and
        is free to delete the payslip first. It does, and the database
        refuses it. Recalculating a run was impossible until the flush below
        was put between the two loops.
        """
        views = await self.payslips(organization_id=organization_id, run_id=run_id)
        for view in views:
            for line in view.lines:
                await self._session.delete(line)
        await self._session.flush()
        for view in views:
            await self._session.delete(view.payslip)
        await self._session.flush()

    # --------------------------------------------------------- adjustments

    async def adjustments(
        self, *, organization_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[PayrollAdjustment]:
        stmt = (
            select(PayrollAdjustment)
            .where(
                PayrollAdjustment.organization_id == organization_id,
                PayrollAdjustment.run_id == run_id,
            )
            .order_by(PayrollAdjustment.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add_adjustment(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        employee_id: uuid.UUID,
        kind: AdjustmentKind,
        amount: Decimal,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollAdjustment:
        """A bonus or a deduction for one person, which must say why.

        The reason is required by the database as well as here. It is the
        control the workbook lacked: nine corrections typed into its payroll
        sheet, none with a stated reason (`WORKBOOK_FORMULA_AUDIT.md` P8).
        """
        run = await self.get(organization_id=organization_id, run_id=run_id)
        if run.status not in OPEN_RUN_STATUSES:
            raise Conflict(
                f"This run is {run.status.value.lower()}. An adjustment to a "
                "settled run is a new run's business.",
                details={"status": run.status.value},
            )
        if amount <= ZERO:
            raise ValidationFailed("An adjustment must be a positive amount")
        if not (reason or "").strip():
            raise ValidationFailed("An adjustment has to say why")
        await self._employees.get(organization_id=organization_id, employee_id=employee_id)

        adjustment = PayrollAdjustment(
            organization_id=organization_id,
            run_id=run_id,
            employee_id=employee_id,
            kind=kind,
            amount=amount,
            reason=reason.strip(),
            authorised_by_user_id=actor_user_id,
        )
        self._session.add(adjustment)
        await self._session.flush()
        self._audit.record(
            events.PAYROLL_ADJUSTED,
            organization_id=organization_id,
            target_type="payroll_adjustment",
            target_id=adjustment.id,
            target_label=f"{kind.value} on {run.code}",
            business_event=f"payroll_adjustment:{adjustment.id}",
            actor_user_id=actor_user_id,
            new_values={
                "kind": kind.value,
                "amount": str(amount),
                "reason": adjustment.reason,
                "employee_id": str(employee_id),
            },
        )
        return adjustment

    # ------------------------------------------------ approve, post, pay

    async def approve(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollRun:
        run = await self.get(organization_id=organization_id, run_id=run_id)
        if run.status is not PayrollRunStatus.CALCULATED:
            raise Conflict(
                f"Only a calculated run can be approved; this one is "
                f"{run.status.value.lower()}",
                details={"status": run.status.value},
            )
        if (
            actor_user_id is not None
            and run.calculated_by_user_id is not None
            and actor_user_id == run.calculated_by_user_id
        ):
            raise Conflict(
                "The person who calculated the payroll cannot approve it. "
                "Somebody else has to agree the figures.",
                details={"control": "payroll.run.approve"},
            )
        await self._refuse_if_blocked(organization_id=organization_id, run=run)
        await self._refuse_if_stale(organization_id=organization_id, run=run)

        run.status = PayrollRunStatus.APPROVED
        run.approved_at = datetime.now(UTC)
        run.approved_by_user_id = actor_user_id
        # Nothing blocks an approved run, so the note saying otherwise goes.
        # It is a snapshot written when the figures were worked out, and a
        # browser pass showed a settled run still listing reasons it had
        # long since satisfied.
        run.blocked_reasons = None
        await self._session.flush()
        self._audit.record(
            events.PAYROLL_APPROVED,
            organization_id=organization_id,
            target_type="payroll_run",
            target_id=run.id,
            target_label=run.code,
            business_event=f"payroll_approved:{run.id}",
            actor_user_id=actor_user_id,
            new_values={"status": run.status.value},
        )
        return run

    async def _refuse_if_stale(self, *, organization_id: uuid.UUID, run: PayrollRun) -> None:
        """The payslips must have been worked out under the rules now in force.

        Checking that every required rule *is* approved is not enough, and a
        browser pass proved it: a run calculated while nothing was approved,
        and approved after somebody accepted the rates, carried payslips with
        no tax on them at all. Every rule it needed existed by then, so the
        missing-rule check passed and the clinic posted a month of wages with
        nothing withheld.

        So the question asked here is the stricter one — were these figures
        produced under *these* rules — and the answer is a comparison of two
        moments. A rule accepted after the run was last calculated means the
        figures predate it, and the run has to be calculated again.
        """
        if run.calculated_at is None:
            raise Conflict(
                "This run has not been calculated yet.",
                details={"status": run.status.value},
            )
        accepted = await self._rules.latest_approval_in_force(
            organization_id=organization_id, on_date=run.period_end
        )
        if accepted is not None and accepted > run.calculated_at:
            raise Conflict(
                "A statutory rule was approved after this payroll was "
                "calculated, so its payslips were worked out under different "
                "rules. Calculate it again before approving it.",
                details={
                    "calculated_at": run.calculated_at.isoformat(),
                    "rule_approved_at": accepted.isoformat(),
                },
            )

    async def _refuse_if_blocked(self, *, organization_id: uuid.UUID, run: PayrollRun) -> None:
        """Re-checked here, not trusted from the row written at calculation."""
        blocked = await self.blocking_reasons(
            organization_id=organization_id, on_date=run.period_end
        )
        if blocked:
            run.blocked_reasons = json.dumps(blocked)
            raise Conflict(
                "This payroll cannot go further until the rules it depends on "
                "are approved: " + " ".join(blocked),
                details={"blocked_by": blocked},
            )

    async def post(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollRun:
        """Put the cost and the debts into the books. One entry per run.

        The expense is the gross plus the employer's own contributions; the
        credits are what is owed to staff and to the budget. Paying anybody
        happens later and settles the debt this creates — so nothing here is
        recorded twice.
        """
        run = await self.get(organization_id=organization_id, run_id=run_id)
        if run.status is not PayrollRunStatus.APPROVED:
            raise Conflict(
                f"Only an approved run can be posted; this one is "
                f"{run.status.value.lower()}",
                details={"status": run.status.value},
            )
        await self._refuse_if_blocked(organization_id=organization_id, run=run)
        await self._refuse_if_stale(organization_id=organization_id, run=run)

        views = await self.payslips(organization_id=organization_id, run_id=run_id)
        if not views:
            raise ValidationFailed("This run has no payslips to post")

        gross = quantize(sum((view.payslip.gross for view in views), ZERO), run.currency)
        withheld: dict[StatutoryRuleKind, Decimal] = {}
        employer: dict[StatutoryRuleKind, Decimal] = {}
        for view in views:
            for line in view.lines:
                if line.code not in {kind.value for kind in StatutoryRuleKind}:
                    continue
                kind = StatutoryRuleKind(line.code)
                if line.category is PayslipLineCategory.EMPLOYEE_DEDUCTION:
                    withheld[kind] = withheld.get(kind, ZERO) + line.amount
                elif line.category is PayslipLineCategory.EMPLOYER_CONTRIBUTION:
                    employer[kind] = employer.get(kind, ZERO) + line.amount

        # One entry for the whole accrual, with a pair of lines per operation
        # — which is the clinic's own set of operations, combined. Several
        # entries would also have to share one idempotency key, and this way
        # the books show the month's payroll as the single event it was.
        policy = await PolicyService(self._session, self._audit).policy_in_force_on(
            organization_id=organization_id, on=run.period_end
        )

        async def rule_for(operation_code: str):
            return await self._posting.resolve_policy_rule(
                organization_id=organization_id,
                policy_id=policy.id,
                operation_code=operation_code,
            )

        wages = await rule_for(OPERATION_WAGES)
        lines = [
            PostingLine(account_id=wages.debit_account_id, debit=gross),
            PostingLine(account_id=wages.credit_account_id, credit=gross),
        ]

        # The income tax leaves the wages payable in full, including the
        # INPS part, which a second pair then moves on to the pension
        # account — exactly as the workbook's rows 36 and 37 do.
        income_tax = withheld.get(StatutoryRuleKind.INCOME_TAX, ZERO)
        inps = withheld.get(StatutoryRuleKind.INPS, ZERO)
        if income_tax + inps > ZERO:
            rule = await rule_for(OPERATION_INCOME_TAX)
            lines += [
                PostingLine(account_id=rule.debit_account_id, debit=income_tax + inps),
                PostingLine(account_id=rule.credit_account_id, credit=income_tax + inps),
            ]
        if inps > ZERO:
            rule = await rule_for(OPERATION_INPS)
            lines += [
                PostingLine(account_id=rule.debit_account_id, debit=inps),
                PostingLine(account_id=rule.credit_account_id, credit=inps),
            ]
        pension = withheld.get(StatutoryRuleKind.EMPLOYEE_PENSION, ZERO)
        if pension > ZERO:
            rule = await rule_for(OPERATION_EMPLOYEE_PENSION)
            lines += [
                PostingLine(account_id=rule.debit_account_id, debit=pension),
                PostingLine(account_id=rule.credit_account_id, credit=pension),
            ]
        social = employer.get(StatutoryRuleKind.SOCIAL_TAX, ZERO)
        if social > ZERO:
            rule = await rule_for(OPERATION_SOCIAL_TAX)
            lines += [
                PostingLine(account_id=rule.debit_account_id, debit=social),
                PostingLine(account_id=rule.credit_account_id, credit=social),
            ]

        entry = await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=run.period_end,
                description=f"Payroll {run.year}-{run.month:02d} ({run.code})",
                source_kind=SourceKind.PAYROLL_RUN,
                source_id=str(run.id),
                purpose=PostingPurpose.ACCRUAL,
                currency=run.currency,
                operation_code=OPERATION_WAGES,
                policy_id=policy.id,
                lines=lines,
            ),
            actor_user_id=actor_user_id,
        )

        run.status = PayrollRunStatus.POSTED
        run.posted_at = datetime.now(UTC)
        run.posted_by_user_id = actor_user_id
        run.journal_entry_id = entry.id
        await self._session.flush()
        self._audit.record(
            events.PAYROLL_POSTED,
            organization_id=organization_id,
            target_type="payroll_run",
            target_id=run.id,
            target_label=run.code,
            business_event=f"payroll_posted:{run.id}",
            actor_user_id=actor_user_id,
            new_values={
                "gross": str(gross),
                "withheld": {kind.value: str(value) for kind, value in withheld.items()},
                "employer": {kind.value: str(value) for kind, value in employer.items()},
                "journal_entry_id": str(entry.id),
            },
        )
        return run

    async def mark_paid(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        from_cash: bool = False,
        paid_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollRun:
        """Settle what is owed to staff. Recognises no expense.

        A separate act from posting, because the debt and its settlement are
        separate events — and because a clinic that pays late still incurred
        the cost in the month the work was done.
        """
        run = await self.get(organization_id=organization_id, run_id=run_id)
        if run.status is not PayrollRunStatus.POSTED:
            raise Conflict(
                f"Only a posted run can be paid; this one is {run.status.value.lower()}",
                details={"status": run.status.value},
            )
        views = await self.payslips(organization_id=organization_id, run_id=run_id)
        net = quantize(sum((view.payslip.net for view in views), ZERO), run.currency)
        if net <= ZERO:
            raise ValidationFailed("There is nothing to pay on this run")

        policy = await PolicyService(self._session, self._audit).policy_in_force_on(
            organization_id=organization_id, on=paid_on or run.period_end
        )
        operation = OPERATION_PAID_CASH if from_cash else OPERATION_PAID_BANK
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=operation,
        )
        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=paid_on or run.period_end,
                description=f"Payroll {run.year}-{run.month:02d} ({run.code}): net pay",
                source_kind=SourceKind.PAYROLL_RUN,
                source_id=str(run.id),
                purpose=PostingPurpose.PAYMENT,
                currency=run.currency,
                operation_code=operation,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=net),
                    PostingLine(account_id=rule.credit_account_id, credit=net),
                ],
            ),
            actor_user_id=actor_user_id,
        )
        run.status = PayrollRunStatus.PAID
        await self._session.flush()
        self._audit.record(
            events.PAYROLL_PAID,
            organization_id=organization_id,
            target_type="payroll_run",
            target_id=run.id,
            target_label=run.code,
            business_event=f"payroll_paid_event:{run.id}",
            actor_user_id=actor_user_id,
            new_values={"net": str(net), "from_cash": from_cash},
        )
        return run

    async def cancel(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> PayrollRun:
        run = await self.get(organization_id=organization_id, run_id=run_id)
        if run.status not in OPEN_RUN_STATUSES:
            raise Conflict(
                "A run that has been approved or posted cannot be cancelled. "
                "Correct it with an authorised adjustment on a later run.",
                details={"status": run.status.value},
            )
        if not (reason or "").strip():
            raise ValidationFailed("A cancelled run has to say why")
        run.status = PayrollRunStatus.CANCELLED
        run.cancelled_reason = reason.strip()
        await self._session.flush()
        self._audit.record(
            events.PAYROLL_CANCELLED,
            organization_id=organization_id,
            target_type="payroll_run",
            target_id=run.id,
            target_label=run.code,
            actor_user_id=actor_user_id,
            new_values={"reason": run.cancelled_reason},
        )
        return run


def employee_label(employee: object) -> str:
    return display_name(employee)  # type: ignore[arg-type]
