"""Accounting endpoints: chart of accounts, periods, journal, policies, reports.

Every write is gated by a permission, and the sensitive ones — posting,
reversing, closing a period, changing a policy — are audited by the services
they call.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.errors import Conflict, ValidationFailed
from app.core.money import ZERO, quantize
from app.modules.accounting.enums import (
    EntryStatus,
    PostingPurpose,
    SourceKind,
)
from app.modules.accounting.models import Account, JournalEntry
from app.modules.accounting.schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountStatementResponse,
    AccountWithBalance,
    FiscalYearCreateRequest,
    FiscalYearResponse,
    JournalEntryCreateRequest,
    JournalEntryDetail,
    JournalEntryResponse,
    JournalLineDetail,
    PeriodResponse,
    PolicyCreateRequest,
    PolicyResponse,
    PolicyRuleDetail,
    PolicyRuleRequest,
    PolicyRuleResponse,
    ReopenPeriodRequest,
    ReverseEntryRequest,
    SeedChartRequest,
    SeedChartResponse,
    TrialBalanceResponse,
    TrialBalanceRowResponse,
)
from app.modules.accounting.services.accounts import AccountService
from app.modules.accounting.services.default_policy import DefaultPolicyService
from app.modules.accounting.services.periods import PeriodService
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import (
    PostingLine,
    PostingRequest,
    PostingService,
)
from app.modules.accounting.services.reports import ReportService

router = APIRouter(prefix="/accounting", tags=["accounting"])


# --------------------------------------------------------------- accounts


@router.get(
    "/accounts",
    response_model=list[AccountResponse],
    dependencies=[Depends(require_permissions("accounting.account.read"))],
)
async def list_accounts(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    postable_only: bool = Query(default=False),
    active_only: bool = Query(default=True),
    search: str | None = Query(default=None, max_length=64),
) -> list[AccountResponse]:
    """The organization's chart of accounts."""
    service = AccountService(db, audit)
    accounts = await service.list_accounts(
        organization_id=context.require_organization(),
        postable_only=postable_only,
        active_only=active_only,
        search=search,
    )
    return [AccountResponse.model_validate(a) for a in accounts]


@router.get(
    "/accounts/{account_id}",
    response_model=AccountWithBalance,
    dependencies=[Depends(require_permissions("accounting.account.read"))],
)
async def get_account(
    account_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> AccountWithBalance:
    """One account with its balance from posted entries."""
    organization_id = context.require_organization()
    service = AccountService(db, audit)
    account = await service.get_account(organization_id=organization_id, account_id=account_id)
    balance = await service.balance(organization_id=organization_id, account_id=account_id)
    return AccountWithBalance(
        **AccountResponse.model_validate(account).model_dump(), balance=balance
    )


@router.post(
    "/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("accounting.account.manage"))],
)
async def create_account(
    payload: AccountCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> AccountResponse:
    """Add an account to the chart."""
    service = AccountService(db, audit)
    account = await service.create_account(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        level=payload.level,
        nature=payload.nature,
        element=payload.element,
        parent_code=payload.parent_code,
        description=payload.description,
    )
    await db.commit()
    return AccountResponse.model_validate(account)


@router.post(
    "/accounts/{account_id}/deactivate",
    response_model=AccountResponse,
    dependencies=[Depends(require_permissions("accounting.account.manage"))],
)
async def deactivate_account(
    account_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    reason: str | None = Query(default=None, max_length=500),
) -> AccountResponse:
    """Stop new postings to an account. Refused while it holds a balance."""
    service = AccountService(db, audit)
    account = await service.set_active(
        organization_id=context.require_organization(),
        account_id=account_id,
        is_active=False,
        reason=reason,
    )
    await db.commit()
    return AccountResponse.model_validate(account)


@router.post(
    "/accounts/seed-statutory-chart",
    response_model=SeedChartResponse,
    dependencies=[Depends(require_permissions("accounting.account.manage"))],
)
async def seed_statutory_chart(
    payload: SeedChartRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> SeedChartResponse:
    """Import the Uzbek national chart of accounts.

    Every imported account is marked `needs_confirmation`: its statutory
    classification was derived from code ranges rather than read from the
    source, and an accountant must sign it off (OPEN_QUESTIONS A1).
    """
    service = AccountService(db, audit)
    summary = await service.seed_statutory_chart(
        organization_id=context.require_organization(),
        only_commonly_used=payload.only_commonly_used,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return SeedChartResponse(
        **summary,
        notice=(
            "Imported accounts are marked as needing confirmation. Their "
            "classification is derived, not authoritative — have an accountant "
            "review it before relying on statutory reports."
        ),
    )


# ------------------------------------------------------- periods and years


@router.get(
    "/fiscal-years",
    response_model=list[FiscalYearResponse],
    dependencies=[Depends(require_permissions("organization.period.read"))],
)
async def list_fiscal_years(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[FiscalYearResponse]:
    service = PeriodService(db, audit)
    years = await service.list_fiscal_years(context.require_organization())
    return [FiscalYearResponse.model_validate(y) for y in years]


@router.post(
    "/fiscal-years",
    response_model=FiscalYearResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("organization.period.manage"))],
)
async def create_fiscal_year(
    payload: FiscalYearCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> FiscalYearResponse:
    """Open a fiscal year and, by default, its monthly periods."""
    service = PeriodService(db, audit)
    year = await service.create_fiscal_year(
        organization_id=context.require_organization(),
        code=payload.code,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        create_monthly_periods=payload.create_monthly_periods,
    )
    await db.commit()
    return FiscalYearResponse.model_validate(year)


@router.get(
    "/periods",
    response_model=list[PeriodResponse],
    dependencies=[Depends(require_permissions("organization.period.read"))],
)
async def list_periods(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    fiscal_year_id: uuid.UUID | None = Query(default=None),
) -> list[PeriodResponse]:
    service = PeriodService(db, audit)
    periods = await service.list_periods(context.require_organization(), fiscal_year_id)
    return [PeriodResponse.model_validate(p) for p in periods]


@router.post(
    "/periods/{period_id}/close",
    response_model=PeriodResponse,
    dependencies=[Depends(require_permissions("organization.period.manage"))],
)
async def close_period(
    period_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> PeriodResponse:
    """Close a period. Refused while it still holds unposted drafts."""
    service = PeriodService(db, audit)
    period = await service.close_period(
        organization_id=context.require_organization(),
        period_id=period_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return PeriodResponse.model_validate(period)


@router.post(
    "/periods/{period_id}/reopen",
    response_model=PeriodResponse,
    dependencies=[Depends(require_permissions("organization.period.manage"))],
)
async def reopen_period(
    period_id: uuid.UUID,
    payload: ReopenPeriodRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PeriodResponse:
    """Reopen a closed period. Requires a reason and is audited."""
    service = PeriodService(db, audit)
    period = await service.reopen_period(
        organization_id=context.require_organization(),
        period_id=period_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return PeriodResponse.model_validate(period)


# ---------------------------------------------------------------- journal


def _with_total(entry: JournalEntry, total: Decimal) -> JournalEntryResponse:
    """Build the response for an entry, adding its computed total.

    `total` is derived from the lines rather than stored — a stored total
    would be a second version of the facts.
    """
    response = JournalEntryResponse.model_validate(entry)
    response.total = quantize(total, entry.currency)
    return response


def _with_rule_count(policy, rule_count: int) -> PolicyResponse:
    response = PolicyResponse.model_validate(policy)
    response.rule_count = rule_count
    return response


async def _entry_response(
    db: DbSession,
    audit: AuditDep,
    organization_id: uuid.UUID,
    entry: JournalEntry,
) -> JournalEntryDetail:
    """Expand an entry into its API shape, with account codes resolved."""
    service = PostingService(db, audit)
    lines = await service.get_lines(organization_id=organization_id, entry_id=entry.id)
    accounts = {
        a.id: a
        for a in (
            await db.execute(
                select(Account).where(
                    Account.organization_id == organization_id,
                    Account.id.in_([line.account_id for line in lines]),
                )
            )
        ).scalars()
    }
    total: Decimal = sum((line.debit for line in lines), ZERO)
    return JournalEntryDetail(
        # `model_validate(entry)` reads attributes, so a column the ORM never
        # assigned (a NULL reversal link on a freshly created row) is still
        # seen as None. `entry.__dict__` omits it entirely and fails.
        **_with_total(entry, total).model_dump(),
        lines=[
            JournalLineDetail(
                id=line.id,
                line_number=line.line_number,
                account_id=line.account_id,
                debit=quantize(line.debit, entry.currency),
                credit=quantize(line.credit, entry.currency),
                description=line.description,
                account_code=accounts[line.account_id].code,
                account_name=accounts[line.account_id].name,
            )
            for line in lines
        ],
    )


@router.get(
    "/journal",
    response_model=list[JournalEntryResponse],
    dependencies=[Depends(require_permissions("accounting.journal.read"))],
)
async def list_journal_entries(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    entry_status: EntryStatus | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JournalEntryResponse]:
    """Journal entries, newest first."""
    organization_id = context.require_organization()
    stmt = select(JournalEntry).where(JournalEntry.organization_id == organization_id)
    if entry_status is not None:
        stmt = stmt.where(JournalEntry.status == entry_status)
    if from_date is not None:
        stmt = stmt.where(JournalEntry.entry_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(JournalEntry.entry_date <= to_date)
    stmt = stmt.order_by(JournalEntry.entry_date.desc(), JournalEntry.entry_number.desc()).limit(
        limit
    )

    entries = list((await db.execute(stmt)).scalars().all())
    service = PostingService(db, audit)
    result: list[JournalEntryResponse] = []
    for entry in entries:
        lines = await service.get_lines(organization_id=organization_id, entry_id=entry.id)
        total: Decimal = sum((line.debit for line in lines), ZERO)
        result.append(_with_total(entry, total))
    return result


@router.get(
    "/journal/{entry_id}",
    response_model=JournalEntryDetail,
    dependencies=[Depends(require_permissions("accounting.journal.read"))],
)
async def get_journal_entry(
    entry_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> JournalEntryDetail:
    organization_id = context.require_organization()
    entry = await PostingService(db, audit).get_entry(
        organization_id=organization_id, entry_id=entry_id
    )
    return await _entry_response(db, audit, organization_id, entry)


@router.post(
    "/journal",
    response_model=JournalEntryDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("accounting.journal.draft"))],
)
async def create_journal_entry(
    payload: JournalEntryCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> JournalEntryDetail:
    """Create a manual journal entry, optionally posting it at once.

    Posting requires `accounting.journal.post` *in addition* to the draft
    permission, so somebody who may prepare entries cannot post them unless
    they also hold that authority.
    """
    organization_id = context.require_organization()
    if payload.post_immediately:
        context.require("accounting.journal.post")
    if not payload.reason or not payload.reason.strip():
        # Invariant 6: a manual entry has no originating business event, so
        # the justification is what makes it traceable.
        raise ValidationFailed(
            "A manual journal entry requires a reason",
            details={"field": "reason"},
        )

    request = PostingRequest(
        entry_date=payload.entry_date,
        description=payload.description,
        source_kind=SourceKind.MANUAL,
        purpose=PostingPurpose.MANUAL,
        currency=payload.currency,
        reason=payload.reason.strip(),
        lines=[
            PostingLine(
                account_code=line.account_code,
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
            for line in payload.lines
        ],
    )
    service = PostingService(db, audit)
    entry = (
        await service.post(
            organization_id=organization_id,
            request=request,
            actor_user_id=context.user_id,
        )
        if payload.post_immediately
        else await service.create_draft(
            organization_id=organization_id,
            request=request,
            actor_user_id=context.user_id,
        )
    )
    await db.commit()
    return await _entry_response(db, audit, organization_id, entry)


@router.post(
    "/journal/{entry_id}/post",
    response_model=JournalEntryDetail,
    dependencies=[Depends(require_permissions("accounting.journal.post"))],
)
async def post_journal_entry(
    entry_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> JournalEntryDetail:
    """Post a draft to the ledger. Irreversible except by reversal."""
    organization_id = context.require_organization()
    service = PostingService(db, audit)
    entry = await service.post_draft(
        organization_id=organization_id,
        entry_id=entry_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _entry_response(db, audit, organization_id, entry)


@router.post(
    "/journal/{entry_id}/reverse",
    response_model=JournalEntryDetail,
    dependencies=[Depends(require_permissions("accounting.journal.reverse"))],
)
async def reverse_journal_entry(
    entry_id: uuid.UUID,
    payload: ReverseEntryRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> JournalEntryDetail:
    """Post the offsetting entry. The original is never edited."""
    organization_id = context.require_organization()
    service = PostingService(db, audit)
    reversal = await service.reverse(
        organization_id=organization_id,
        entry_id=entry_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
        reversal_date=payload.reversal_date,
    )
    await db.commit()
    return await _entry_response(db, audit, organization_id, reversal)


@router.delete(
    "/journal/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permissions("accounting.journal.draft"))],
)
async def delete_draft_entry(
    entry_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> None:
    """Delete a draft. A posted entry cannot be deleted — reverse it."""
    organization_id = context.require_organization()
    service = PostingService(db, audit)
    entry = await service.get_entry(organization_id=organization_id, entry_id=entry_id)
    if entry.status is EntryStatus.POSTED:
        raise Conflict(
            f"Entry {entry.entry_number} is posted and cannot be deleted. Reverse it instead.",
            details={"entry_number": entry.entry_number},
        )
    await db.delete(entry)
    await db.commit()


# --------------------------------------------------------------- policies


@router.get(
    "/policies",
    response_model=list[PolicyResponse],
    dependencies=[Depends(require_permissions("accounting.policy.read"))],
)
async def list_policies(context: OrgCtx, db: DbSession, audit: AuditDep) -> list[PolicyResponse]:
    organization_id = context.require_organization()
    service = PolicyService(db, audit)
    policies = await service.list_policies(organization_id)
    result = []
    for policy in policies:
        rules = await service.list_rules(organization_id=organization_id, policy_id=policy.id)
        result.append(_with_rule_count(policy, len(rules)))
    return result


@router.get(
    "/policies/{policy_id}/rules",
    response_model=list[PolicyRuleDetail],
    dependencies=[Depends(require_permissions("accounting.policy.read"))],
)
async def list_policy_rules(
    policy_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[PolicyRuleDetail]:
    organization_id = context.require_organization()
    service = PolicyService(db, audit)
    await service.get_policy(organization_id=organization_id, policy_id=policy_id)
    rules = await service.list_rules(organization_id=organization_id, policy_id=policy_id)
    codes = {
        a.id: a.code
        for a in (
            await db.execute(select(Account).where(Account.organization_id == organization_id))
        ).scalars()
    }
    return [
        PolicyRuleDetail(
            **PolicyRuleResponse.model_validate(rule).model_dump(),
            debit_account_code=codes.get(rule.debit_account_id, "?"),
            credit_account_code=codes.get(rule.credit_account_id, "?"),
        )
        for rule in rules
    ]


@router.post(
    "/policies",
    response_model=PolicyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("accounting.policy.manage"))],
)
async def create_policy_draft(
    payload: PolicyCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> PolicyResponse:
    """Start a new policy version. Drafts affect no posting until activated."""
    service = PolicyService(db, audit)
    policy = await service.create_draft(
        organization_id=context.require_organization(),
        name=payload.name,
        effective_from=payload.effective_from,
        code=payload.code,
        notes=payload.notes,
    )
    await db.commit()
    return PolicyResponse.model_validate(policy)


@router.put(
    "/policies/{policy_id}/rules",
    response_model=PolicyRuleDetail,
    dependencies=[Depends(require_permissions("accounting.policy.manage"))],
)
async def set_policy_rule(
    policy_id: uuid.UUID,
    payload: PolicyRuleRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PolicyRuleDetail:
    """Add or replace one operation mapping. Only on a draft version."""
    service = PolicyService(db, audit)
    rule = await service.set_rule(
        organization_id=context.require_organization(),
        policy_id=policy_id,
        operation_code=payload.operation_code,
        description=payload.description,
        debit_account_code=payload.debit_account_code,
        credit_account_code=payload.credit_account_code,
        description_uz=payload.description_uz,
        description_ru=payload.description_ru,
        required_documents=payload.required_documents,
    )
    await db.commit()
    return PolicyRuleDetail(
        **PolicyRuleResponse.model_validate(rule).model_dump(),
        debit_account_code=payload.debit_account_code,
        credit_account_code=payload.credit_account_code,
    )


@router.post(
    "/policies/{policy_id}/activate",
    response_model=PolicyResponse,
    dependencies=[Depends(require_permissions("accounting.policy.manage"))],
)
async def activate_policy(
    policy_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> PolicyResponse:
    """Validate and put a policy version into force.

    Activating never restates history: posted entries store the accounts they
    used and the version that chose them.
    """
    organization_id = context.require_organization()
    service = PolicyService(db, audit)
    policy = await service.activate(
        organization_id=organization_id,
        policy_id=policy_id,
        actor_user_id=context.user_id,
    )
    rules = await service.list_rules(organization_id=organization_id, policy_id=policy_id)
    await db.commit()
    summary = PolicyResponse.model_validate(policy)
    summary.rule_count = len(rules)
    return summary


@router.delete(
    "/policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permissions("accounting.policy.manage"))],
)
async def discard_policy_draft(
    policy_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> None:
    """Throw away a draft version. An active one must be superseded instead.

    A draft's effective date cannot be edited, so without this a draft that
    starts before the version in force can never be activated — and a draft
    blocks every new version until it is resolved.
    """
    await PolicyService(db, audit).discard_draft(
        organization_id=context.require_organization(), policy_id=policy_id
    )
    await db.commit()


@router.post(
    "/policies/seed-default",
    response_model=PolicyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("accounting.policy.manage"))],
)
async def seed_default_policy(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    effective_from: date = Query(..., description="The date this policy starts"),
) -> PolicyResponse:
    """Build a starting policy from the legacy workbook's Dr/Cr mappings.

    Creates a **draft**. Nothing posts under it until somebody activates it,
    and each rule records where in the workbook it came from so a reviewer can
    check it rather than take it on trust. Rules the workbook's own author
    flagged as doubtful are not included (OPEN_QUESTIONS A4).
    """
    organization_id = context.require_organization()
    policy = await DefaultPolicyService(db, audit).seed(
        organization_id=organization_id,
        effective_from=effective_from,
        actor_user_id=context.user_id,
    )
    rules = await PolicyService(db, audit).list_rules(
        organization_id=organization_id, policy_id=policy.id
    )
    await db.commit()
    return _with_rule_count(policy, len(rules))


# ---------------------------------------------------------------- reports


@router.get(
    "/reports/trial-balance",
    response_model=TrialBalanceResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def trial_balance(
    context: OrgCtx,
    db: DbSession,
    from_date: date = Query(...),
    to_date: date = Query(...),
    include_zero: bool = Query(default=False),
) -> TrialBalanceResponse:
    """Opening, movement and closing balance per account.

    Derived from posted journal lines at request time — never from a stored
    total, which would be a second version of the facts.
    """
    if to_date < from_date:
        raise ValidationFailed("to_date must not be before from_date")
    report = await ReportService(db).trial_balance(
        organization_id=context.require_organization(),
        from_date=from_date,
        to_date=to_date,
        include_zero=include_zero,
    )
    return TrialBalanceResponse(
        from_date=report.from_date,
        to_date=report.to_date,
        currency=report.currency,
        rows=[TrialBalanceRowResponse(**asdict(row)) for row in report.rows],
        total_opening_debit=report.total_opening_debit,
        total_opening_credit=report.total_opening_credit,
        total_period_debit=report.total_period_debit,
        total_period_credit=report.total_period_credit,
        total_closing_debit=report.total_closing_debit,
        total_closing_credit=report.total_closing_credit,
        is_balanced=report.is_balanced,
    )


@router.get(
    "/reports/account-statement/{account_id}",
    response_model=AccountStatementResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def account_statement(
    account_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    from_date: date = Query(...),
    to_date: date = Query(...),
) -> AccountStatementResponse:
    """One account's movements with a running balance."""
    if to_date < from_date:
        raise ValidationFailed("to_date must not be before from_date")
    statement = await ReportService(db).account_statement(
        organization_id=context.require_organization(),
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )
    return AccountStatementResponse(
        **{k: v for k, v in asdict(statement).items() if k != "lines"},
        lines=[asdict(line) for line in statement.lines],
    )
