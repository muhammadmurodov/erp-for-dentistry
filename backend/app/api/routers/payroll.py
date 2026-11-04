"""Payroll: the rules, the agreements, and the monthly run.

**Nothing here carries a statutory rate.** The rates live in the rules table,
each with its effective date, its legal reference and its approver. This
router can propose one and approve one; it cannot contain one.

Four authority boundaries, which are why the endpoints are split as they are:

* **Reading** a run (`payroll.run.read`) is separate from calculating it.
* **Calculating** (`payroll.run.calculate`) works out what is owed and
  commits nothing to the books.
* **Approving** (`payroll.run.approve`, sensitive and audited) says the
  figures are right — and the service refuses it to whoever calculated them,
  even when one person holds both permissions, which in a clinic of three
  they will.
* **Posting and paying** (`payroll.run.post`) are the acts that touch the
  ledger, and both are refused while any required statutory rule is
  unapproved. The refusal names the rule and the open question behind it.

Editing the rules themselves needs `payroll.rules.manage`, which is its own
sensitive permission: changing a tax rate is not a clerical act.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.schemas import MoneyMixin
from app.modules.payroll.enums import (
    AdjustmentKind,
    CompensationKind,
    PayrollRunStatus,
    PayslipLineCategory,
    RuleMethod,
    RuleStatus,
    StatutoryRuleKind,
)
from app.modules.payroll.services.policy import CompensationService, StatutoryRuleService
from app.modules.payroll.services.runs import PayrollRunService

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _reject_float(value: object) -> object:
    """A rate of 11.9 read as a binary float is 11.899999999999999."""
    if isinstance(value, float):
        raise ValueError("Pass an amount or a rate as a string or a decimal, not a float")
    return value


# ----------------------------------------------------------- statutory rules


class RuleCreateRequest(BaseModel):
    kind: StatutoryRuleKind
    method: RuleMethod = RuleMethod.PERCENT_OF_GROSS
    effective_from: date
    rate: Decimal | None = None
    fixed_amount: Decimal | None = None
    effective_to: date | None = None
    component_of: StatutoryRuleKind | None = None
    legal_reference: str | None = None
    source_url: str | None = None
    applicability: str | None = None
    notes: str | None = None

    _no_floats = field_validator("rate", "fixed_amount", mode="before")(_reject_float)


class RuleResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    kind: StatutoryRuleKind
    method: RuleMethod
    rate: Decimal | None
    fixed_amount: Decimal | None
    effective_from: date
    effective_to: date | None
    component_of: StatutoryRuleKind | None
    legal_reference: str | None
    source_url: str | None
    applicability: str | None
    notes: str | None
    status: RuleStatus
    approved_by_user_id: uuid.UUID | None


@router.get(
    "/rules",
    response_model=list[RuleResponse],
    dependencies=[Depends(require_permissions("payroll.run.read"))],
)
async def list_rules(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    kind: StatutoryRuleKind | None = Query(default=None),
    rule_status: RuleStatus | None = Query(default=None, alias="status"),
) -> list[RuleResponse]:
    rows = await StatutoryRuleService(db, audit).list_rules(
        organization_id=context.require_organization(), kind=kind, status=rule_status
    )
    return [RuleResponse.model_validate(row, from_attributes=True) for row in rows]


@router.post(
    "/rules",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("payroll.rules.manage"))],
)
async def propose_rule(
    payload: RuleCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RuleResponse:
    """Write down a rate. It applies to nothing until somebody approves it."""
    rule = await StatutoryRuleService(db, audit).propose(
        organization_id=context.require_organization(),
        actor_user_id=context.user_id,
        **payload.model_dump(),
    )
    await db.commit()
    return RuleResponse.model_validate(rule, from_attributes=True)


@router.post(
    "/rules/seed",
    response_model=list[RuleResponse],
    dependencies=[Depends(require_permissions("payroll.rules.manage"))],
)
async def seed_rules(context: OrgCtx, db: DbSession, audit: AuditDep) -> list[RuleResponse]:
    """Propose the rates the workbook audit verified — as drafts, with sources.

    The social tax is deliberately not among them: its current rate could
    not be confirmed (`OPEN_QUESTIONS.md` P2), and an unverified rate is not
    a rate. Payroll stays blocked until an accountant enters it.
    """
    created = await StatutoryRuleService(db, audit).seed_verified_drafts(
        organization_id=context.require_organization(), actor_user_id=context.user_id
    )
    await db.commit()
    return [RuleResponse.model_validate(row, from_attributes=True) for row in created]


@router.post(
    "/rules/{rule_id}/approve",
    response_model=RuleResponse,
    dependencies=[Depends(require_permissions("payroll.rules.manage"))],
)
async def approve_rule(
    rule_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RuleResponse:
    """Accept a rate as the one to apply. Requires its legal reference."""
    rule = await StatutoryRuleService(db, audit).approve(
        organization_id=context.require_organization(),
        rule_id=rule_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return RuleResponse.model_validate(rule, from_attributes=True)


# ------------------------------------------------------------- compensation


class CompensationRequest(BaseModel):
    amount: Decimal
    effective_from: date
    note: str | None = None

    _no_floats = field_validator("amount", mode="before")(_reject_float)


class CompensationResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    employee_id: uuid.UUID
    kind: CompensationKind
    amount: Decimal
    currency: str
    effective_from: date
    effective_to: date | None
    note: str | None


@router.get(
    "/employees/{employee_id}/compensation",
    response_model=list[CompensationResponse],
    dependencies=[Depends(require_permissions("payroll.run.read"))],
)
async def list_compensation(
    employee_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[CompensationResponse]:
    rows = await CompensationService(db, audit).list_for_employee(
        organization_id=context.require_organization(), employee_id=employee_id
    )
    return [CompensationResponse.model_validate(row, from_attributes=True) for row in rows]


@router.post(
    "/employees/{employee_id}/compensation",
    response_model=CompensationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("payroll.rules.manage"))],
)
async def set_compensation(
    employee_id: uuid.UUID,
    payload: CompensationRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> CompensationResponse:
    """Agree a salary from a date. A rise is a new row, never an edit."""
    compensation = await CompensationService(db, audit).set_salary(
        organization_id=context.require_organization(),
        employee_id=employee_id,
        amount=payload.amount,
        effective_from=payload.effective_from,
        note=payload.note,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return CompensationResponse.model_validate(compensation, from_attributes=True)


# -------------------------------------------------------------------- runs


class RunCreateRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    working_days: int = Field(gt=0, le=31)
    notes: str | None = None


class AdjustmentRequest(BaseModel):
    employee_id: uuid.UUID
    kind: AdjustmentKind
    amount: Decimal
    reason: str = Field(min_length=1)

    _no_floats = field_validator("amount", mode="before")(_reject_float)


class PaymentRequest(BaseModel):
    from_cash: bool = False
    paid_on: date | None = None


class CancelRequest(BaseModel):
    reason: str = Field(min_length=1)


class RunResponse(BaseModel):
    id: uuid.UUID
    code: str
    year: int
    month: int
    period_start: date
    period_end: date
    working_days: int
    currency: str
    status: PayrollRunStatus
    #: Why this run cannot be approved or posted, in words somebody can act
    #: on. Empty when nothing stands in the way.
    blocked_reasons: list[str]
    journal_entry_id: uuid.UUID | None
    notes: str | None


class PayslipLineResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    category: PayslipLineCategory
    code: str
    description: str
    amount: Decimal
    rate: Decimal | None
    statutory_rule_id: uuid.UUID | None
    note: str | None


class PayslipResponse(BaseModel, MoneyMixin):
    id: uuid.UUID
    employee_id: uuid.UUID
    worked_days: Decimal
    working_days: int
    monthly_salary: Decimal
    gross: Decimal
    employee_deductions: Decimal
    employer_contributions: Decimal
    net: Decimal
    lines: list[PayslipLineResponse]


def _run_response(run, blocked: list[str]) -> RunResponse:
    return RunResponse(
        id=run.id,
        code=run.code,
        year=run.year,
        month=run.month,
        period_start=run.period_start,
        period_end=run.period_end,
        working_days=run.working_days,
        currency=run.currency,
        status=run.status,
        blocked_reasons=blocked,
        journal_entry_id=run.journal_entry_id,
        notes=run.notes,
    )


def _stored_reasons(run) -> list[str]:
    if not run.blocked_reasons:
        return []
    try:
        return list(json.loads(run.blocked_reasons))
    except ValueError:
        return []


@router.get(
    "/runs",
    response_model=list[RunResponse],
    dependencies=[Depends(require_permissions("payroll.run.read"))],
)
async def list_runs(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    year: int | None = Query(default=None),
) -> list[RunResponse]:
    rows = await PayrollRunService(db, audit).list_runs(
        organization_id=context.require_organization(), year=year
    )
    return [_run_response(run, _stored_reasons(run)) for run in rows]


@router.post(
    "/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("payroll.run.calculate"))],
)
async def open_run(
    payload: RunCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RunResponse:
    service = PayrollRunService(db, audit)
    run = await service.open_run(
        organization_id=context.require_organization(),
        year=payload.year,
        month=payload.month,
        working_days=payload.working_days,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    blocked = await service.blocking_reasons(
        organization_id=context.require_organization(), on_date=run.period_end
    )
    await db.commit()
    return _run_response(run, blocked)


@router.get(
    "/runs/{run_id}",
    response_model=RunResponse,
    dependencies=[Depends(require_permissions("payroll.run.read"))],
)
async def get_run(
    run_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RunResponse:
    service = PayrollRunService(db, audit)
    organization_id = context.require_organization()
    run = await service.get(organization_id=organization_id, run_id=run_id)
    blocked = await service.blocking_reasons(
        organization_id=organization_id, on_date=run.period_end
    )
    return _run_response(run, blocked)


@router.get(
    "/runs/{run_id}/payslips",
    response_model=list[PayslipResponse],
    dependencies=[Depends(require_permissions("payroll.run.read"))],
)
async def list_payslips(
    run_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[PayslipResponse]:
    views = await PayrollRunService(db, audit).payslips(
        organization_id=context.require_organization(), run_id=run_id
    )
    return [
        PayslipResponse(
            id=view.payslip.id,
            employee_id=view.payslip.employee_id,
            worked_days=view.payslip.worked_days,
            working_days=view.payslip.working_days,
            monthly_salary=view.payslip.monthly_salary,
            gross=view.payslip.gross,
            employee_deductions=view.payslip.employee_deductions,
            employer_contributions=view.payslip.employer_contributions,
            net=view.payslip.net,
            lines=[
                PayslipLineResponse.model_validate(line, from_attributes=True)
                for line in view.lines
            ],
        )
        for view in views
    ]


@router.post(
    "/runs/{run_id}/calculate",
    response_model=RunResponse,
    dependencies=[Depends(require_permissions("payroll.run.calculate"))],
)
async def calculate_run(
    run_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RunResponse:
    """Work out every payslip. Commits nothing to the ledger."""
    service = PayrollRunService(db, audit)
    run = await service.calculate(
        organization_id=context.require_organization(),
        run_id=run_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return _run_response(run, _stored_reasons(run))


@router.post(
    "/runs/{run_id}/adjustments",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("payroll.run.calculate"))],
)
async def add_adjustment(
    run_id: uuid.UUID,
    payload: AdjustmentRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> RunResponse:
    """A bonus or a deduction for one person, which must say why."""
    service = PayrollRunService(db, audit)
    organization_id = context.require_organization()
    await service.add_adjustment(
        organization_id=organization_id,
        run_id=run_id,
        employee_id=payload.employee_id,
        kind=payload.kind,
        amount=payload.amount,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    run = await service.get(organization_id=organization_id, run_id=run_id)
    await db.commit()
    return _run_response(run, _stored_reasons(run))


@router.post(
    "/runs/{run_id}/approve",
    response_model=RunResponse,
    dependencies=[Depends(require_permissions("payroll.run.approve"))],
)
async def approve_run(
    run_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RunResponse:
    """Agree the figures. Refused to whoever calculated them."""
    service = PayrollRunService(db, audit)
    run = await service.approve(
        organization_id=context.require_organization(),
        run_id=run_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return _run_response(run, [])


@router.post(
    "/runs/{run_id}/post",
    response_model=RunResponse,
    dependencies=[Depends(require_permissions("payroll.run.post"))],
)
async def post_run(
    run_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> RunResponse:
    """Put the cost and the debts into the books. One entry for the run."""
    service = PayrollRunService(db, audit)
    run = await service.post(
        organization_id=context.require_organization(),
        run_id=run_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return _run_response(run, [])


@router.post(
    "/runs/{run_id}/pay",
    response_model=RunResponse,
    dependencies=[Depends(require_permissions("payroll.run.post"))],
)
async def pay_run(
    run_id: uuid.UUID,
    payload: PaymentRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> RunResponse:
    """Settle what is owed to staff. Recognises no expense."""
    service = PayrollRunService(db, audit)
    run = await service.mark_paid(
        organization_id=context.require_organization(),
        run_id=run_id,
        from_cash=payload.from_cash,
        paid_on=payload.paid_on,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return _run_response(run, [])


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunResponse,
    dependencies=[Depends(require_permissions("payroll.run.calculate"))],
)
async def cancel_run(
    run_id: uuid.UUID,
    payload: CancelRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> RunResponse:
    service = PayrollRunService(db, audit)
    run = await service.cancel(
        organization_id=context.require_organization(),
        run_id=run_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return _run_response(run, _stored_reasons(run))
