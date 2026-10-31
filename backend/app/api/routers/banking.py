"""Bank account, statement import and reconciliation endpoints.

Importing a statement posts nothing. Matching posts only where an entry is
genuinely due — clearing a transit account or recording a withheld fee —
because a payment, a receipt, a settlement, a statement line and a journal
entry are stages of one transaction, not five sources of revenue.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.errors import ValidationFailed
from app.core.money import quantize
from app.modules.banking.enums import TransactionStatus
from app.modules.banking.models import BankAccount, BankTransaction
from app.modules.banking.schemas import (
    BankAccountCreateRequest,
    BankAccountResponse,
    BankTransactionDetail,
    BankTransactionResponse,
    ColumnMappingDefaults,
    ColumnMappingRequest,
    DeactivateBankAccountRequest,
    IgnoreTransactionRequest,
    ImportResultResponse,
    MatchCandidateResponse,
    MatchRequestBody,
    MatchResponse,
    StatementImportResponse,
)
from app.modules.banking.services.accounts import BankAccountService, masked_account_number
from app.modules.banking.services.reconciliation import MatchRequest, ReconciliationService
from app.modules.banking.services.statement_import import (
    DEFAULT_COLUMN_MAPPING,
    StatementImporter,
)

router = APIRouter(prefix="/banking", tags=["banking"])

#: A statement is a spreadsheet of a few thousand rows. Anything much larger
#: is a mistake, and reading it into memory to find out is not worth it.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_SUFFIXES = (".xlsx", ".xlsm", ".csv")


def _account_response(account: BankAccount) -> BankAccountResponse:
    return BankAccountResponse(
        id=account.id,
        code=account.code,
        name=account.name,
        bank_name=account.bank_name,
        mfo=account.mfo,
        account_number_masked=masked_account_number(account.account_number),
        account_id=account.account_id,
        currency=account.currency,
        is_active=account.is_active,
        has_column_mapping=bool(account.column_mapping),
    )


# --------------------------------------------------------------- accounts


@router.get(
    "/accounts",
    response_model=list[BankAccountResponse],
    dependencies=[Depends(require_permissions("banking.account.read"))],
)
async def list_bank_accounts(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    include_inactive: bool = Query(default=False),
) -> list[BankAccountResponse]:
    accounts = await BankAccountService(db, audit).list_accounts(
        organization_id=context.require_organization(),
        include_inactive=include_inactive,
    )
    return [_account_response(a) for a in accounts]


@router.post(
    "/accounts",
    response_model=BankAccountResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("banking.account.manage"))],
)
async def create_bank_account(
    payload: BankAccountCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> BankAccountResponse:
    account = await BankAccountService(db, audit).create(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        account_code=payload.account_code,
        bank_name=payload.bank_name,
        mfo=payload.mfo,
        account_number=payload.account_number,
        currency=payload.currency,
        column_mapping=payload.column_mapping,
    )
    await db.commit()
    return _account_response(account)


@router.get(
    "/column-mapping/defaults",
    response_model=ColumnMappingDefaults,
    dependencies=[Depends(require_permissions("banking.account.read"))],
)
async def column_mapping_defaults() -> ColumnMappingDefaults:
    """The column headings the importer already recognises.

    Shown in the UI so an operator can see whether their bank's export needs
    a mapping at all before uploading anything.
    """
    return ColumnMappingDefaults(aliases=DEFAULT_COLUMN_MAPPING)


@router.put(
    "/accounts/{bank_account_id}/column-mapping",
    response_model=BankAccountResponse,
    dependencies=[Depends(require_permissions("banking.account.manage"))],
)
async def set_column_mapping(
    bank_account_id: uuid.UUID,
    payload: ColumnMappingRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> BankAccountResponse:
    account = await BankAccountService(db, audit).set_column_mapping(
        organization_id=context.require_organization(),
        bank_account_id=bank_account_id,
        column_mapping=payload.column_mapping,
    )
    await db.commit()
    return _account_response(account)


@router.post(
    "/accounts/{bank_account_id}/deactivate",
    response_model=BankAccountResponse,
    dependencies=[Depends(require_permissions("banking.account.manage"))],
)
async def deactivate_bank_account(
    bank_account_id: uuid.UUID,
    payload: DeactivateBankAccountRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> BankAccountResponse:
    account = await BankAccountService(db, audit).deactivate(
        organization_id=context.require_organization(),
        bank_account_id=bank_account_id,
        reason=payload.reason,
    )
    await db.commit()
    return _account_response(account)


# -------------------------------------------------------------- statements


@router.post(
    "/accounts/{bank_account_id}/statements",
    response_model=ImportResultResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("banking.statement.import"))],
)
async def import_statement(
    bank_account_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    file: UploadFile = File(...),
) -> ImportResultResponse:
    filename = file.filename or "statement"
    if not filename.lower().endswith(ALLOWED_SUFFIXES):
        raise ValidationFailed(
            "Upload the statement as .xlsx or .csv, as your bank exports it",
            details={"filename": filename, "accepted": list(ALLOWED_SUFFIXES)},
        )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationFailed(
            f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
            "Export a narrower date range.",
        )
    if not content:
        raise ValidationFailed("The uploaded file is empty")

    result = await StatementImporter(db, audit).import_statement(
        organization_id=context.require_organization(),
        bank_account_id=bank_account_id,
        filename=filename,
        content=content,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return ImportResultResponse(**asdict(result))


@router.get(
    "/statements",
    response_model=list[StatementImportResponse],
    dependencies=[Depends(require_permissions("banking.account.read"))],
)
async def list_statement_imports(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    bank_account_id: uuid.UUID | None = Query(default=None),
) -> list[StatementImportResponse]:
    imports = await StatementImporter(db, audit).list_imports(
        organization_id=context.require_organization(),
        bank_account_id=bank_account_id,
    )
    return [StatementImportResponse.model_validate(i) for i in imports]


# ----------------------------------------------------------- transactions


@router.get(
    "/transactions",
    response_model=list[BankTransactionResponse],
    dependencies=[Depends(require_permissions("banking.account.read"))],
)
async def list_transactions(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    bank_account_id: uuid.UUID | None = Query(default=None),
    transaction_status: TransactionStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[BankTransactionResponse]:
    transactions = await ReconciliationService(db, audit).list_transactions(
        organization_id=context.require_organization(),
        bank_account_id=bank_account_id,
        status=transaction_status,
        limit=limit,
    )
    return [BankTransactionResponse.model_validate(t) for t in transactions]


async def _detail(
    db: DbSession, audit: AuditDep, organization_id: uuid.UUID, transaction: BankTransaction
) -> BankTransactionDetail:
    service = ReconciliationService(db, audit)
    matched = await service.matched_amount(
        organization_id=organization_id, transaction_id=transaction.id
    )
    matches = await service.list_matches(
        organization_id=organization_id, transaction_id=transaction.id
    )
    accounted = quantize(matched - transaction.commission_amount, transaction.currency)
    return BankTransactionDetail(
        **BankTransactionResponse.model_validate(transaction).model_dump(),
        matched_amount=quantize(matched, transaction.currency),
        unaccounted_amount=quantize(transaction.amount - accounted, transaction.currency),
        matches=[MatchResponse.model_validate(m) for m in matches],
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=BankTransactionDetail,
    dependencies=[Depends(require_permissions("banking.account.read"))],
)
async def get_transaction(
    transaction_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> BankTransactionDetail:
    organization_id = context.require_organization()
    transaction = await ReconciliationService(db, audit).get_transaction(
        organization_id=organization_id, transaction_id=transaction_id
    )
    return await _detail(db, audit, organization_id, transaction)


@router.get(
    "/transactions/{transaction_id}/candidates",
    response_model=list[MatchCandidateResponse],
    dependencies=[Depends(require_permissions("banking.account.read"))],
)
async def match_candidates(
    transaction_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[MatchCandidateResponse]:
    candidates = await ReconciliationService(db, audit).candidates_for(
        organization_id=context.require_organization(), transaction_id=transaction_id
    )
    return [MatchCandidateResponse(**asdict(c)) for c in candidates]


@router.post(
    "/transactions/{transaction_id}/match",
    response_model=BankTransactionDetail,
    dependencies=[Depends(require_permissions("banking.reconcile"))],
)
async def match_transaction(
    transaction_id: uuid.UUID,
    payload: MatchRequestBody,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> BankTransactionDetail:
    organization_id = context.require_organization()
    transaction = await ReconciliationService(db, audit).match(
        organization_id=organization_id,
        transaction_id=transaction_id,
        requests=[MatchRequest(kind=r.kind, id=r.id, amount=r.amount) for r in payload.records],
        commission=payload.commission,
        note=payload.note,
        actor_user_id=context.user_id,
    )
    detail = await _detail(db, audit, organization_id, transaction)
    await db.commit()
    return detail


@router.post(
    "/transactions/{transaction_id}/ignore",
    response_model=BankTransactionResponse,
    dependencies=[Depends(require_permissions("banking.reconcile"))],
)
async def ignore_transaction(
    transaction_id: uuid.UUID,
    payload: IgnoreTransactionRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> BankTransactionResponse:
    transaction = await ReconciliationService(db, audit).ignore(
        organization_id=context.require_organization(),
        transaction_id=transaction_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return BankTransactionResponse.model_validate(transaction)


@router.post(
    "/transactions/{transaction_id}/reopen",
    response_model=BankTransactionResponse,
    dependencies=[Depends(require_permissions("banking.reconcile"))],
)
async def reopen_transaction(
    transaction_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> BankTransactionResponse:
    transaction = await ReconciliationService(db, audit).reopen(
        organization_id=context.require_organization(),
        transaction_id=transaction_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return BankTransactionResponse.model_validate(transaction)
