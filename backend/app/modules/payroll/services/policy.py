"""The two kinds of payroll rule, and what it takes to approve one.

**No statutory rate is written in code anywhere in this system.** It exists
only as a row here, with the date it took effect, the authority it comes
from and the person who approved it. A rate that is merely written down
calculates nothing: `DRAFT` rules are invisible to the engine.

`seed_verified_drafts` proposes the rates the workbook audit was able to
verify against authoritative sources, each carrying its reference and its
effective date — and proposes **nothing else**. The social tax rate is
absent on purpose: the audit could not confirm the current one
(`OPEN_QUESTIONS.md` P2), and an unverified rate is not a rate. Payroll
therefore refuses to post until an accountant enters and approves it, which
is the intended outcome rather than a gap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.hr.services.employees import EmployeeService
from app.modules.payroll.enums import (
    CompensationKind,
    RuleMethod,
    RuleStatus,
    StatutoryRuleKind,
)
from app.modules.payroll.models import EmployeeCompensation, StatutoryRule
from app.modules.payroll.services.calculation import RuleInput


class StatutoryRuleService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def list_rules(
        self,
        *,
        organization_id: uuid.UUID,
        kind: StatutoryRuleKind | None = None,
        status: RuleStatus | None = None,
    ) -> list[StatutoryRule]:
        stmt = select(StatutoryRule).where(StatutoryRule.organization_id == organization_id)
        if kind is not None:
            stmt = stmt.where(StatutoryRule.kind == kind)
        if status is not None:
            stmt = stmt.where(StatutoryRule.status == status)
        stmt = stmt.order_by(StatutoryRule.kind, StatutoryRule.effective_from.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, *, organization_id: uuid.UUID, rule_id: uuid.UUID) -> StatutoryRule:
        stmt = select(StatutoryRule).where(
            StatutoryRule.organization_id == organization_id, StatutoryRule.id == rule_id
        )
        rule = (await self._session.execute(stmt)).scalar_one_or_none()
        if rule is None:
            raise NotFound("Payroll rule not found")
        return rule

    async def propose(
        self,
        *,
        organization_id: uuid.UUID,
        kind: StatutoryRuleKind,
        method: RuleMethod,
        effective_from: date,
        rate: Decimal | None = None,
        fixed_amount: Decimal | None = None,
        effective_to: date | None = None,
        component_of: StatutoryRuleKind | None = None,
        legal_reference: str | None = None,
        source_url: str | None = None,
        applicability: str | None = None,
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> StatutoryRule:
        """Write down a rate. It applies to nothing until it is approved."""
        if method is RuleMethod.PERCENT_OF_GROSS and rate is None:
            raise ValidationFailed("A percentage rule needs a rate")
        if method is RuleMethod.FIXED_AMOUNT and fixed_amount is None:
            raise ValidationFailed("A fixed-amount rule needs an amount")
        if rate is not None and rate < 0:
            raise ValidationFailed("A rate cannot be negative")
        if effective_to is not None and effective_to < effective_from:
            raise ValidationFailed("A rule cannot end before it begins")
        if component_of is not None and component_of == kind:
            raise ValidationFailed("A rule cannot be a component of itself")

        rule = StatutoryRule(
            organization_id=organization_id,
            kind=kind,
            method=method,
            rate=rate,
            fixed_amount=fixed_amount,
            effective_from=effective_from,
            effective_to=effective_to,
            component_of=component_of,
            legal_reference=(legal_reference or "").strip() or None,
            source_url=(source_url or "").strip() or None,
            applicability=(applicability or "").strip() or None,
            notes=(notes or "").strip() or None,
            status=RuleStatus.DRAFT,
        )
        self._session.add(rule)
        await self._session.flush()
        self._audit.record(
            events.PAYROLL_RULE_PROPOSED,
            organization_id=organization_id,
            target_type="payroll_statutory_rule",
            target_id=rule.id,
            target_label=f"{kind.value} from {effective_from.isoformat()}",
            actor_user_id=actor_user_id,
            new_values={
                "kind": kind.value,
                "method": method.value,
                "rate": str(rate) if rate is not None else None,
                "effective_from": effective_from.isoformat(),
                "legal_reference": rule.legal_reference,
            },
        )
        return rule

    async def approve(
        self,
        *,
        organization_id: uuid.UUID,
        rule_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> StatutoryRule:
        """Accept a rate as the one to apply, and close the one it replaces.

        Approval demands a legal reference. Somebody may believe a rate; to
        approve it they have to say where it comes from, and that sentence
        is what an auditor reads two years later.
        """
        rule = await self.get(organization_id=organization_id, rule_id=rule_id)
        if rule.status is not RuleStatus.DRAFT:
            raise Conflict(
                f"This rule is {rule.status.value.lower()} and cannot be approved again",
                details={"status": rule.status.value},
            )
        if not (rule.legal_reference or "").strip():
            raise ValidationFailed(
                "A statutory rate cannot be approved without the authority it "
                "comes from. Record the law, decree or article first.",
                details={"field": "legal_reference"},
            )

        # The rule this one replaces stops the day before it starts. The
        # earlier rule is never edited out of existence: historical runs
        # cite it and must stay readable.
        superseded = []
        closes_on = rule.effective_from - timedelta(days=1)
        for existing in await self.list_rules(
            organization_id=organization_id, kind=rule.kind, status=RuleStatus.APPROVED
        ):
            if existing.id == rule.id:
                continue
            if existing.effective_to is not None and existing.effective_to < rule.effective_from:
                continue
            if existing.effective_from >= rule.effective_from:
                raise Conflict(
                    "An approved rule of this kind already starts on or after "
                    "that date. Withdraw it first, or date this one earlier.",
                    details={"conflicting_rule_id": str(existing.id)},
                )
            existing.effective_to = closes_on
            superseded.append(existing)

        rule.status = RuleStatus.APPROVED
        rule.approved_by_user_id = actor_user_id
        rule.approved_at = datetime.now(UTC)
        await self._session.flush()

        self._audit.record(
            events.PAYROLL_RULE_APPROVED,
            organization_id=organization_id,
            target_type="payroll_statutory_rule",
            target_id=rule.id,
            target_label=f"{rule.kind.value} from {rule.effective_from.isoformat()}",
            business_event=f"payroll_rule_approved:{rule.id}",
            actor_user_id=actor_user_id,
            new_values={
                "kind": rule.kind.value,
                "rate": str(rule.rate) if rule.rate is not None else None,
                "effective_from": rule.effective_from.isoformat(),
                "legal_reference": rule.legal_reference,
                "superseded": [str(row.id) for row in superseded],
            },
        )
        for existing in superseded:
            self._audit.record(
                events.PAYROLL_RULE_SUPERSEDED,
                organization_id=organization_id,
                target_type="payroll_statutory_rule",
                target_id=existing.id,
                target_label=existing.kind.value,
                actor_user_id=actor_user_id,
                new_values={"effective_to": closes_on.isoformat()},
            )
        return rule

    async def rules_in_force(
        self, *, organization_id: uuid.UUID, on_date: date
    ) -> list[RuleInput]:
        """Approved rules only, as the calculation engine needs them."""
        rules = await self.list_rules(
            organization_id=organization_id, status=RuleStatus.APPROVED
        )
        return [
            RuleInput(
                id=rule.id,
                kind=rule.kind,
                method=rule.method,
                rate=rule.rate,
                fixed_amount=rule.fixed_amount,
                component_of=rule.component_of,
                effective_from=rule.effective_from,
                effective_to=rule.effective_to,
            )
            for rule in rules
            if rule.effective_from <= on_date
            and (rule.effective_to is None or rule.effective_to >= on_date)
        ]

    async def latest_approval_in_force(
        self, *, organization_id: uuid.UUID, on_date: date
    ) -> datetime | None:
        """When the newest rule governing this date was accepted.

        Used to tell a payroll run whether its payslips are still current. A
        run calculated before this moment was worked out under rules that
        have since changed, and the figures on it are stale even though
        every rule it needs is now approved.
        """
        rules = await self.list_rules(
            organization_id=organization_id, status=RuleStatus.APPROVED
        )
        stamps = [
            rule.approved_at
            for rule in rules
            if rule.effective_from <= on_date
            and (rule.effective_to is None or rule.effective_to >= on_date)
            and rule.approved_at is not None
        ]
        return max(stamps) if stamps else None

    async def seed_verified_drafts(
        self, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
    ) -> list[StatutoryRule]:
        """Propose the rates the workbook audit verified — and only those.

        Each is a **draft**: the system proposes what the sources say and an
        accountant decides. Nothing here is approved automatically, and the
        social tax is deliberately missing because its current rate could
        not be confirmed.
        """
        existing = {
            (rule.kind, rule.effective_from)
            for rule in await self.list_rules(organization_id=organization_id)
        }
        proposals = [
            {
                "kind": StatutoryRuleKind.INCOME_TAX,
                "method": RuleMethod.PERCENT_OF_GROSS,
                "rate": Decimal("12"),
                "effective_from": date(2019, 1, 1),
                "legal_reference": (
                    "Single personal income tax rate of 12% from 1 January 2019. "
                    "Still reported as 12% for residents in February 2026."
                ),
                "source_url": "https://buxgalter.uz/publish/doc/text213796_kak_rasschitat_ndfl_i_vznosy_na_inps_za_fevral_2026_goda",
                "notes": (
                    "Verified during the workbook audit (2026-11-04). The "
                    "clinic's own sheet has applied 11.9% plus 0.1% INPS since "
                    "2019-01, which is the same 12%."
                ),
            },
            {
                "kind": StatutoryRuleKind.INPS,
                "method": RuleMethod.PERCENT_OF_GROSS,
                "rate": Decimal("0.1"),
                "effective_from": date(2019, 1, 1),
                "component_of": StatutoryRuleKind.INCOME_TAX,
                "legal_reference": (
                    "Mandatory monthly contribution to the individual savings "
                    "pension account: 0.1% of the taxable wage, taken out of the "
                    "12% income tax rather than in addition to it."
                ),
                "source_url": "https://buxgalter.uz/publish/doc/text213796_kak_rasschitat_ndfl_i_vznosy_na_inps_za_fevral_2026_goda",
                "notes": (
                    "The clinic's own posting rule (workbook row 37) moves this "
                    "from the income tax payable to 6520.02, which is the same "
                    "statement in accounts."
                ),
            },
            {
                "kind": StatutoryRuleKind.EMPLOYEE_PENSION,
                "method": RuleMethod.PERCENT_OF_GROSS,
                "rate": Decimal("8"),
                "effective_from": date(2018, 1, 1),
                "effective_to": date(2018, 12, 31),
                "legal_reference": (
                    "Employee insurance contribution to the off-budget Pension "
                    "Fund at 8% of wages, abolished from 1 January 2019."
                ),
                "source_url": "https://buxgalter.uz/publish/doc/text146767_esp_i_strahovye_vznosy_grajdan_-_chto_novogo_v_2019_godu",
                "notes": (
                    "Proposed only so periods before 2019 can be entered as "
                    "history. It ends on 2018-12-31 and applies to nothing after."
                ),
            },
        ]
        created: list[StatutoryRule] = []
        for proposal in proposals:
            key = (proposal["kind"], proposal["effective_from"])
            if key in existing:
                continue
            created.append(
                await self.propose(
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    **proposal,  # type: ignore[arg-type]
                )
            )
        return created


class CompensationService:
    """What the clinic agreed to pay each person, and from when."""

    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._employees = EmployeeService(session, audit)

    async def list_for_employee(
        self, *, organization_id: uuid.UUID, employee_id: uuid.UUID
    ) -> list[EmployeeCompensation]:
        stmt = (
            select(EmployeeCompensation)
            .where(
                EmployeeCompensation.organization_id == organization_id,
                EmployeeCompensation.employee_id == employee_id,
            )
            .order_by(EmployeeCompensation.effective_from.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def set_salary(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        amount: Decimal,
        effective_from: date,
        note: str | None = None,
        currency: str = "UZS",
        actor_user_id: uuid.UUID | None = None,
    ) -> EmployeeCompensation:
        """Agree a salary from a date. A rise is a new row, not an edit.

        The row it replaces is closed the day before, so the two never
        overlap and a payslip can always name exactly one agreement per day.
        """
        if amount < 0:
            raise ValidationFailed("A salary cannot be negative")
        await self._employees.get(organization_id=organization_id, employee_id=employee_id)

        existing = await self.list_for_employee(
            organization_id=organization_id, employee_id=employee_id
        )
        for row in existing:
            if row.effective_from == effective_from:
                raise Conflict(
                    "A salary already starts on that date for this employee. "
                    "Change its amount, or choose another date.",
                    details={"compensation_id": str(row.id)},
                )
            if row.effective_from > effective_from:
                raise Conflict(
                    "A later salary agreement already exists for this employee. "
                    "Backdating around it would leave two agreements covering "
                    "one day.",
                    details={"compensation_id": str(row.id)},
                )
            if row.effective_to is None:
                row.effective_to = effective_from - timedelta(days=1)

        compensation = EmployeeCompensation(
            organization_id=organization_id,
            employee_id=employee_id,
            kind=CompensationKind.FIXED_MONTHLY,
            amount=amount,
            currency=currency,
            effective_from=effective_from,
            note=(note or "").strip() or None,
            approved_by_user_id=actor_user_id,
            approved_at=datetime.now(UTC),
        )
        self._session.add(compensation)
        await self._session.flush()
        self._audit.record(
            events.COMPENSATION_SET,
            organization_id=organization_id,
            target_type="employee_compensation",
            target_id=compensation.id,
            target_label=str(employee_id),
            actor_user_id=actor_user_id,
            new_values={
                "amount": str(amount),
                "effective_from": effective_from.isoformat(),
                "note": compensation.note,
            },
        )
        return compensation

    async def covering(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        period_start: date,
        period_end: date,
    ) -> list[EmployeeCompensation]:
        """Every agreement that covers any day of the period, in order.

        More than one means the salary changed mid-period, which the run
        handles by counting the days worked under each from the timesheet
        rather than assuming how they were distributed.
        """
        rows = await self.list_for_employee(
            organization_id=organization_id, employee_id=employee_id
        )
        covering = [
            row
            for row in rows
            if row.effective_from <= period_end
            and (row.effective_to is None or row.effective_to >= period_start)
        ]
        return sorted(covering, key=lambda row: row.effective_from)
