"""Management reports: what was earned, what is owned and owed, what moved.

Everything here is **derived from posted journal entries at the moment it is
asked for**. No report total is stored, so a report cannot disagree with the
ledger — if it did, the trial balance would have to be wrong too, and that is
checked by its own tests.

Three boundaries worth stating:

* **These are management reports, not statutory filings.** Which forms the
  clinic must file, and how their lines map onto accounts, is
  `OPEN_QUESTIONS.md` A2; the cash-flow method is A3. `GET /reporting/catalogue`
  reports those as blocked, by name, and the screen renders that answer
  rather than a hand-written list.
* **An unclassified account is shown, never absorbed.** The seeded chart is
  marked unconfirmed while A1 is unanswered. A statement that quietly folded
  such an account into revenue would be inventing a classification; these put
  them in their own section and count them.
* **Reading a report needs `reporting.financial.read` and nothing else.** It
  is the same permission the trial balance and the account statement already
  use, which is what makes drill-down work: a reader who can see a figure can
  see the entries behind it, and a reader who cannot see the figure never
  reaches either.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.clock import clinic_today
from app.core.errors import ValidationFailed
from app.core.money import ZERO
from app.modules.accounting.services.accounts import AccountService
from app.modules.organization.service import OrganizationService
from app.modules.purchasing.services.payables import PayablesService
from app.modules.purchasing.services.suppliers import SupplierService
from app.modules.reporting.schemas import (
    BalanceSheetResponse,
    CashFlowResponse,
    PayablesAgeingResponse,
    PayablesAgeingRowResponse,
    ProfitAndLossResponse,
    ReportCapability,
    ReportCatalogueResponse,
)
from app.modules.reporting.services.cashflow import CashFlowService
from app.modules.reporting.services.exports import (
    balance_sheet_xlsx,
    cash_flow_xlsx,
    payables_xlsx,
    profit_and_loss_xlsx,
    receivables_xlsx,
)
from app.modules.reporting.services.statements import StatementService
from app.modules.sales.schemas import ReceivablesAgeing
from app.modules.sales.services.receivables import ReceivablesService

router = APIRouter(prefix="/reporting", tags=["reporting"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _window(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise ValidationFailed(
            "The end of the period is before its start",
            details={"from_date": from_date.isoformat(), "to_date": to_date.isoformat()},
        )


def _comparative_window(
    compare_from: date | None, compare_to: date | None
) -> tuple[date, date] | None:
    """A comparative period needs both ends or neither.

    Half a window would have to be guessed — the same month last year? the
    previous month? — and guessing which comparison somebody meant is how a
    report ends up answering a question nobody asked.
    """
    if compare_from is None and compare_to is None:
        return None
    if compare_from is None or compare_to is None:
        raise ValidationFailed(
            "A comparative period needs both a start and an end",
            details={"compare_from": str(compare_from), "compare_to": str(compare_to)},
        )
    _window(compare_from, compare_to)
    return compare_from, compare_to


async def _organization_name(db: DbSession, audit: AuditDep, context: OrgCtx) -> str:
    organization = await OrganizationService(db, audit).get(context.require_organization())
    return organization.short_name or organization.legal_name


def _xlsx(payload: bytes, filename: str) -> Response:
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------- the catalogue


@router.get(
    "/catalogue",
    response_model=ReportCatalogueResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def catalogue(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> ReportCatalogueResponse:
    """What this build can actually report, answered by the backend.

    The screen renders this rather than a list written into the frontend, so
    a report cannot be offered that does not exist and cannot be hidden when
    it does. Statutory forms appear here too — as blocked, naming the
    question that blocks them.
    """
    organization_id = context.require_organization()
    accounts = await AccountService(db, audit).list_accounts(
        organization_id=organization_id, active_only=False
    )
    unconfirmed = sum(1 for account in accounts if account.needs_confirmation)

    reports = [
        ReportCapability(key="profit_and_loss", available=True),
        ReportCapability(key="balance_sheet", available=True),
        ReportCapability(key="receivables_ageing", available=True),
        ReportCapability(key="payables_ageing", available=True),
        ReportCapability(key="cash_flow", available=True),
        ReportCapability(key="trial_balance", available=True),
        ReportCapability(key="account_statement", available=True),
        ReportCapability(
            key="statutory_financial_statements",
            available=False,
            blocked_by="A2",
            statutory=True,
        ),
        ReportCapability(
            key="statutory_cash_flow", available=False, blocked_by="A3", statutory=True
        ),
        ReportCapability(key="vat_return", available=False, blocked_by="A5", statutory=True),
    ]
    return ReportCatalogueResponse(
        reports=reports,
        accounts_needing_confirmation=unconfirmed,
        chart_unconfirmed=unconfirmed > 0,
    )


# ---------------------------------------------------------- profit and loss


@router.get(
    "/profit-and-loss",
    response_model=ProfitAndLossResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def profit_and_loss(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    from_date: date = Query(...),
    to_date: date = Query(...),
    compare_from: date | None = Query(default=None),
    compare_to: date | None = Query(default=None),
) -> ProfitAndLossResponse:
    """Revenue less expenses over a window, with an optional comparison."""
    _window(from_date, to_date)
    comparative = _comparative_window(compare_from, compare_to)
    report = await StatementService(db, audit).profit_and_loss(
        organization_id=context.require_organization(),
        from_date=from_date,
        to_date=to_date,
        compare_from=comparative[0] if comparative else None,
        compare_to=comparative[1] if comparative else None,
    )
    return ProfitAndLossResponse.model_validate(asdict(report))


@router.get(
    "/profit-and-loss/export",
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def export_profit_and_loss(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    from_date: date = Query(...),
    to_date: date = Query(...),
    compare_from: date | None = Query(default=None),
    compare_to: date | None = Query(default=None),
) -> Response:
    _window(from_date, to_date)
    comparative = _comparative_window(compare_from, compare_to)
    report = await StatementService(db, audit).profit_and_loss(
        organization_id=context.require_organization(),
        from_date=from_date,
        to_date=to_date,
        compare_from=comparative[0] if comparative else None,
        compare_to=comparative[1] if comparative else None,
    )
    payload = profit_and_loss_xlsx(
        report, organization_name=await _organization_name(db, audit, context)
    )
    return _xlsx(payload, f"profit-and-loss-{from_date}-{to_date}.xlsx")


# -------------------------------------------------------------- balance sheet


@router.get(
    "/balance-sheet",
    response_model=BalanceSheetResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def balance_sheet(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    as_of: date = Query(...),
    compare_as_of: date | None = Query(default=None),
) -> BalanceSheetResponse:
    """What the clinic owns and owes on one day."""
    report = await StatementService(db, audit).balance_sheet(
        organization_id=context.require_organization(),
        as_of=as_of,
        compare_as_of=compare_as_of,
    )
    return BalanceSheetResponse.model_validate(asdict(report))


@router.get(
    "/balance-sheet/export",
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def export_balance_sheet(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    as_of: date = Query(...),
    compare_as_of: date | None = Query(default=None),
) -> Response:
    report = await StatementService(db, audit).balance_sheet(
        organization_id=context.require_organization(),
        as_of=as_of,
        compare_as_of=compare_as_of,
    )
    payload = balance_sheet_xlsx(
        report, organization_name=await _organization_name(db, audit, context)
    )
    return _xlsx(payload, f"balance-sheet-{as_of}.xlsx")


# ------------------------------------------------------------------- ageing


@router.get(
    "/receivables-ageing",
    response_model=ReceivablesAgeing,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def receivables_ageing(
    context: OrgCtx,
    db: DbSession,
    as_of: date | None = Query(default=None),
) -> ReceivablesAgeing:
    """What patients owe, by how overdue it is.

    The same service the sales module serves at `/sales/receivables/ageing`,
    not a second implementation: a report that computed debt its own way
    would eventually disagree with the screen the cashier uses.
    """
    report = await ReceivablesService(db).ageing(
        organization_id=context.require_organization(), as_of=as_of
    )
    return ReceivablesAgeing.model_validate(asdict(report))


@router.get(
    "/receivables-ageing/export",
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def export_receivables_ageing(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    as_of: date | None = Query(default=None),
) -> Response:
    report = await ReceivablesService(db).ageing(
        organization_id=context.require_organization(), as_of=as_of
    )
    payload = receivables_xlsx(
        report, organization_name=await _organization_name(db, audit, context)
    )
    return _xlsx(payload, f"patients-owing-{report.as_of}.xlsx")


@router.get(
    "/payables-ageing",
    response_model=PayablesAgeingResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def payables_ageing(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    as_of: date | None = Query(default=None),
) -> PayablesAgeingResponse:
    """What the clinic owes suppliers, by how overdue it is.

    Empty is a real answer, and the response says *why* it is empty: a clinic
    with no suppliers yet should see that stated rather than a blank table
    that looks like a failure.
    """
    organization_id = context.require_organization()
    suppliers = await SupplierService(db, audit).list_suppliers(
        organization_id=organization_id, include_inactive=True
    )
    rows = await PayablesService(db, audit).ageing(
        organization_id=organization_id, as_of=as_of
    )
    organization = await OrganizationService(db, audit).get(organization_id)
    return PayablesAgeingResponse(
        as_of=as_of or clinic_today(),
        currency=organization.base_currency,
        rows=[PayablesAgeingRowResponse(**asdict(row)) for row in rows],
        total_outstanding=sum((row.total for row in rows), ZERO),
        no_suppliers_yet=not suppliers,
    )


@router.get(
    "/payables-ageing/export",
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def export_payables_ageing(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    as_of: date | None = Query(default=None),
) -> Response:
    organization_id = context.require_organization()
    rows = await PayablesService(db, audit).ageing(
        organization_id=organization_id, as_of=as_of
    )
    organization = await OrganizationService(db, audit).get(organization_id)
    payload = payables_xlsx(
        rows,
        organization_name=organization.short_name or organization.legal_name,
        as_of=as_of or clinic_today(),
        currency=organization.base_currency,
    )
    return _xlsx(payload, f"owed-to-suppliers-{as_of or clinic_today()}.xlsx")


# ----------------------------------------------------------------- cash flow


@router.get(
    "/cash-flow",
    response_model=CashFlowResponse,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def cash_flow(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    from_date: date = Query(...),
    to_date: date = Query(...),
) -> CashFlowResponse:
    """What came in, what went out, and what it was for.

    Not split into operating, investing and financing: that classification
    is `OPEN_QUESTIONS.md` A3 and nobody has answered it.
    """
    _window(from_date, to_date)
    report = await CashFlowService(db, audit).cash_flow(
        organization_id=context.require_organization(),
        from_date=from_date,
        to_date=to_date,
    )
    return CashFlowResponse.model_validate(asdict(report))


@router.get(
    "/cash-flow/export",
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def export_cash_flow(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    from_date: date = Query(...),
    to_date: date = Query(...),
) -> Response:
    _window(from_date, to_date)
    report = await CashFlowService(db, audit).cash_flow(
        organization_id=context.require_organization(),
        from_date=from_date,
        to_date=to_date,
    )
    payload = cash_flow_xlsx(
        report, organization_name=await _organization_name(db, audit, context)
    )
    return _xlsx(payload, f"cash-movement-{from_date}-{to_date}.xlsx")


# There is deliberately no statutory-form endpoint here. `GET
# /reporting/catalogue` reports those as blocked by A2, A3 and A5, and adding
# one before an accountant confirms the formats and the account mappings
# would be inventing a filing.
