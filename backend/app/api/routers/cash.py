"""Cash register and session endpoints.

The one place where this module reaches into `sales` is taking a cash
payment: it asks sales to create and post the payment, then records that the
money physically arrived in this drawer. Sales stays unaware that cash
sessions exist (ADR-030).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.errors import Conflict
from app.modules.cash.enums import SessionStatus
from app.modules.cash.models import CashSession
from app.modules.cash.schemas import (
    CashDepositRequest,
    CashExpenseRequest,
    CashMovementResponse,
    CashPaymentRequest,
    CashRefundRequest,
    CashRegisterCreateRequest,
    CashRegisterResponse,
    CashSessionDetail,
    CashSessionResponse,
    OpenSessionRequest,
    SessionTotalsResponse,
    SubmitCountRequest,
)
from app.modules.cash.services.sessions import CashSessionService
from app.modules.sales.enums import PaymentMethod
from app.modules.sales.schemas import PaymentResponse
from app.modules.sales.services.billing import BillingService

router = APIRouter(prefix="/cash", tags=["cash"])


async def _session_detail(
    db: DbSession, audit: AuditDep, organization_id: uuid.UUID, session: CashSession
) -> CashSessionDetail:
    service = CashSessionService(db, audit)
    register = await service.get_register(
        organization_id=organization_id, register_id=session.register_id
    )
    totals = await service.totals(organization_id=organization_id, session_id=session.id)
    movements = await service.list_movements(organization_id=organization_id, session_id=session.id)
    return CashSessionDetail(
        **CashSessionResponse.model_validate(session).model_dump(),
        register_code=register.code,
        register_name=register.name,
        totals=SessionTotalsResponse(**asdict(totals)),
        movements=[CashMovementResponse.model_validate(m) for m in movements],
    )


# ------------------------------------------------------------- registers


@router.get(
    "/registers",
    response_model=list[CashRegisterResponse],
    dependencies=[Depends(require_permissions("cash.register.read"))],
)
async def list_registers(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[CashRegisterResponse]:
    registers = await CashSessionService(db, audit).list_registers(
        organization_id=context.require_organization()
    )
    return [CashRegisterResponse.model_validate(r) for r in registers]


@router.post(
    "/registers",
    response_model=CashRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("cash.register.manage"))],
)
async def create_register(
    payload: CashRegisterCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> CashRegisterResponse:
    register = await CashSessionService(db, audit).create_register(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        account_code=payload.account_code,
        currency=payload.currency,
    )
    await db.commit()
    return CashRegisterResponse.model_validate(register)


# -------------------------------------------------------------- sessions


@router.get(
    "/sessions",
    response_model=list[CashSessionResponse],
    dependencies=[Depends(require_permissions("cash.register.read"))],
)
async def list_sessions(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    register_id: uuid.UUID | None = Query(default=None),
    session_status: SessionStatus | None = Query(default=None),
) -> list[CashSessionResponse]:
    sessions = await CashSessionService(db, audit).list_sessions(
        organization_id=context.require_organization(),
        register_id=register_id,
        status=session_status,
    )
    return [CashSessionResponse.model_validate(s) for s in sessions]


@router.get(
    "/sessions/{session_id}",
    response_model=CashSessionDetail,
    dependencies=[Depends(require_permissions("cash.register.read"))],
)
async def get_session(
    session_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> CashSessionDetail:
    """A session with its movements and the derived expected balance."""
    organization_id = context.require_organization()
    session = await CashSessionService(db, audit).get_session(
        organization_id=organization_id, session_id=session_id
    )
    return await _session_detail(db, audit, organization_id, session)


@router.post(
    "/sessions",
    response_model=CashSessionDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("cash.session.operate"))],
)
async def open_session(
    payload: OpenSessionRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> CashSessionDetail:
    """Open a till for the day. Only one session per register may be open."""
    organization_id = context.require_organization()
    session = await CashSessionService(db, audit).open_session(
        organization_id=organization_id,
        register_id=payload.register_id,
        business_date=payload.business_date,
        opening_float=payload.opening_float,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _session_detail(db, audit, organization_id, session)


@router.post(
    "/sessions/{session_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.payment.record"))],
)
async def take_cash_payment(
    session_id: uuid.UUID,
    payload: CashPaymentRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PaymentResponse:
    """Take a cash payment into this drawer.

    Two records, one transaction: `sales` creates and posts the payment,
    `cash` records that the money arrived here so it counts toward the
    expected balance.
    """
    organization_id = context.require_organization()
    billing = BillingService(db, audit)
    cash = CashSessionService(db, audit)

    # Fail before touching sales if the drawer is not open.
    session = await cash.get_session(organization_id=organization_id, session_id=session_id)
    if session.status is not SessionStatus.OPEN:
        raise Conflict(
            f"This cash session is {session.status.value.lower()} and cannot take a payment",
            details={"status": session.status.value},
        )

    payment = await billing.record_payment(
        organization_id=organization_id,
        customer_id=payload.customer_id,
        amount=payload.amount,
        method=PaymentMethod.CASH,
        received_on=payload.received_on,
        currency=session.currency,
        reference=payload.reference,
        notes=payload.notes,
        allocate_to=[
            (uuid.UUID(str(a["invoice_id"])), Decimal(str(a["amount"])))
            for a in payload.allocations
        ],
        auto_allocate=payload.auto_allocate,
        actor_user_id=context.user_id,
    )
    await cash.record_payment_receipt(
        organization_id=organization_id,
        session_id=session_id,
        payment_id=payment.id,
        amount=payment.amount,
        description=f"Payment {payment.number}",
        actor_user_id=context.user_id,
    )
    await db.commit()

    allocated = await billing.allocated_from_payment(
        organization_id=organization_id, payment_id=payment.id
    )
    response = PaymentResponse.model_validate(payment)
    response.allocated = allocated
    response.advance = payment.amount - allocated
    return response


@router.post(
    "/sessions/{session_id}/refunds",
    response_model=PaymentResponse,
    dependencies=[Depends(require_permissions("sales.refund.approve"))],
)
async def refund_cash_payment(
    session_id: uuid.UUID,
    payload: CashRefundRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PaymentResponse:
    """Give a cash payment back out of this drawer.

    The mirror of taking one, and here for the same reason: `sales` reverses
    every entry the payment produced, `cash` records that the money left this
    till. Reversing the ledger without the drawer movement left the session
    expecting cash that had been handed back, so the day's count reported a
    shortage and real money was written off as a variance (ADR-040).

    The drawer is this one, not necessarily the one that took the payment: a
    refund given the next day comes out of the till that is open now.
    """
    organization_id = context.require_organization()
    billing = BillingService(db, audit)
    cash = CashSessionService(db, audit)

    # Fail before reversing anything if the drawer is not open.
    session = await cash.get_session(organization_id=organization_id, session_id=session_id)
    if session.status is not SessionStatus.OPEN:
        raise Conflict(
            f"This cash session is {session.status.value.lower()} and cannot refund a payment",
            details={"status": session.status.value},
        )

    payment = await billing.get_payment(
        organization_id=organization_id, payment_id=payload.payment_id
    )
    if payment.method is not PaymentMethod.CASH:
        raise Conflict(
            "That payment was not taken in cash, so it is not refunded from a "
            "drawer. Refund it through the payment it was taken by.",
            details={"method": payment.method.value},
        )

    refunded = await billing.refund_payment(
        organization_id=organization_id,
        payment_id=payload.payment_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await cash.record_payment_refund(
        organization_id=organization_id,
        session_id=session_id,
        payment_id=refunded.id,
        amount=refunded.amount,
        description=f"Refund of {refunded.number}",
        actor_user_id=context.user_id,
    )
    await db.commit()

    response = PaymentResponse.model_validate(refunded)
    response.allocated = Decimal("0")
    response.advance = Decimal("0")
    return response


@router.post(
    "/sessions/{session_id}/expenses",
    response_model=CashSessionDetail,
    dependencies=[Depends(require_permissions("cash.session.operate"))],
)
async def record_expense(
    session_id: uuid.UUID,
    payload: CashExpenseRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> CashSessionDetail:
    """Money paid out of the till."""
    organization_id = context.require_organization()
    service = CashSessionService(db, audit)
    await service.record_expense(
        organization_id=organization_id,
        session_id=session_id,
        amount=payload.amount,
        description=payload.description,
        actor_user_id=context.user_id,
    )
    await db.commit()
    session = await service.get_session(organization_id=organization_id, session_id=session_id)
    return await _session_detail(db, audit, organization_id, session)


@router.post(
    "/sessions/{session_id}/deposits",
    response_model=CashSessionDetail,
    dependencies=[Depends(require_permissions("cash.session.operate"))],
)
async def record_deposit(
    session_id: uuid.UUID,
    payload: CashDepositRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> CashSessionDetail:
    """Send cash to the bank.

    Posts to a transit account rather than the bank account: the money has
    left the till but has not yet arrived, and the transit balance is exactly
    the list of deposits a statement has not yet confirmed.
    """
    organization_id = context.require_organization()
    service = CashSessionService(db, audit)
    await service.record_deposit(
        organization_id=organization_id,
        session_id=session_id,
        amount=payload.amount,
        route=payload.route,
        reference=payload.reference,
        actor_user_id=context.user_id,
    )
    await db.commit()
    session = await service.get_session(organization_id=organization_id, session_id=session_id)
    return await _session_detail(db, audit, organization_id, session)


@router.post(
    "/sessions/{session_id}/count",
    response_model=CashSessionDetail,
    dependencies=[Depends(require_permissions("cash.count.submit"))],
)
async def submit_count(
    session_id: uuid.UUID,
    payload: SubmitCountRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> CashSessionDetail:
    """Enter the physically counted amount.

    The system compares it with the expected balance and records the
    difference. A difference must be explained.
    """
    organization_id = context.require_organization()
    service = CashSessionService(db, audit)
    session = await service.submit_count(
        organization_id=organization_id,
        session_id=session_id,
        counted_amount=payload.counted_amount,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _session_detail(db, audit, organization_id, session)


@router.post(
    "/sessions/{session_id}/close",
    response_model=CashSessionDetail,
    dependencies=[Depends(require_permissions("cash.session.operate"))],
)
async def close_session(
    session_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> CashSessionDetail:
    """Close a counted session, posting any discrepancy.

    A session that balanced closes on the cashier's own authority. One that
    did not needs `cash.discrepancy.approve`, held by somebody other than the
    person who counted it.
    """
    organization_id = context.require_organization()
    service = CashSessionService(db, audit)
    session = await service.close_session(
        organization_id=organization_id,
        session_id=session_id,
        actor_user_id=context.user_id,
        actor_may_approve=context.has("cash.discrepancy.approve"),
        # The owner is exempt from the segregation-of-duties pair: a
        # single-owner clinic has nobody else (ADR-018).
        actor_is_owner=context.has("accounting.policy.manage"),
    )
    await db.commit()
    return await _session_detail(db, audit, organization_id, session)
