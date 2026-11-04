"""Supplier, request, order, receipt, bill and payment endpoints.

What posts and what does not is the thing to know before reading further:

* Raising a **request** and raising an **order** post nothing. A request is
  somebody asking; an order is a promise to buy. Neither moves an asset.
* **Receiving** posts nothing either — see `services/receiving.py` for why
  that is a decision rather than an omission.
* **Registering a bill** posts, under the policy, and the bill becomes
  immutable from that moment.
* **Paying** posts. A cash payment also moves the drawer, which is why it is
  handled by a composite in the cash module rather than here.

The segregation-of-duties pair `purchasing.order.manage` /
`purchasing.order.approve` is real and enforced in the service, relaxed only
by the organization's own declaration (ADR-041) and recorded when it is.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    AuditDep,
    DbSession,
    DutySeparation,
    OrgCtx,
    require_permissions,
)
from app.core.errors import ValidationFailed
from app.modules.cash.services.supplier_settlement import SupplierCashSettlementService
from app.modules.purchasing.enums import (
    BillStatus,
    OrderStatus,
    RequestStatus,
    SupplierPaymentMethod,
)
from app.modules.purchasing.models import (
    PurchaseOrder,
    SupplierInvoice,
    SupplierPayment,
)
from app.modules.purchasing.schemas import (
    AllocationResponse,
    ApplyAdvanceRequest,
    BillCreateRequest,
    BillLineResponse,
    GoodsReceiptDetail,
    GoodsReceiptResponse,
    OrderCreateRequest,
    OrderLineResponse,
    OrderStatusRequest,
    OutstandingLineResponse,
    PayablesAgeingRowResponse,
    PaymentCreateRequest,
    PurchaseOrderDetail,
    PurchaseOrderResponse,
    PurchaseRequestDetail,
    PurchaseRequestResponse,
    ReceiptCreateRequest,
    ReceiptLineResponse,
    RequestCreateRequest,
    RequestLineResponse,
    RequestStatusRequest,
    ReverseAllocationRequest,
    SupplierBalanceResponse,
    SupplierCreateRequest,
    SupplierInvoiceDetail,
    SupplierInvoiceResponse,
    SupplierPaymentResponse,
    SupplierResponse,
    SupplierUpdateRequest,
    UninvoicedLineResponse,
    VoidBillRequest,
)
from app.modules.purchasing.services.payables import (
    BillLineInput,
    PayablesService,
)
from app.modules.purchasing.services.receiving import ReceiptLineInput, ReceivingService
from app.modules.purchasing.services.suppliers import (
    OrderLineInput,
    PurchaseOrderService,
    PurchaseRequestService,
    RequestLineInput,
    SupplierService,
)

router = APIRouter(prefix="/purchasing", tags=["purchasing"])


async def _supplier_names(
    *, db: DbSession, audit: AuditDep, organization_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    rows = await SupplierService(db, audit).list_suppliers(
        organization_id=organization_id, include_inactive=True
    )
    return {s.id: s.name for s in rows}


def _order_response(order: PurchaseOrder, names: dict[uuid.UUID, str]) -> PurchaseOrderResponse:
    return PurchaseOrderResponse(
        id=order.id,
        code=order.code,
        supplier_id=order.supplier_id,
        supplier_name=names.get(order.supplier_id, "—"),
        request_id=order.request_id,
        status=order.status,
        ordered_on=order.ordered_on,
        expected_on=order.expected_on,
        currency=order.currency,
        total=order.total,
        notes=order.notes,
        raised_by_user_id=order.raised_by_user_id,
        approved_by_user_id=order.approved_by_user_id,
        approved_at=order.approved_at,
        self_approved=order.self_approved,
        closed_reason=order.closed_reason,
    )


# ------------------------------------------------------------------ suppliers


@router.get(
    "/suppliers",
    response_model=list[SupplierResponse],
    dependencies=[Depends(require_permissions("purchasing.supplier.read"))],
)
async def list_suppliers(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    q: str | None = Query(default=None, max_length=128),
    include_inactive: bool = Query(default=False),
) -> list[SupplierResponse]:
    rows = await SupplierService(db, audit).list_suppliers(
        organization_id=context.require_organization(),
        include_inactive=include_inactive,
        query=q,
    )
    return [SupplierResponse.model_validate(s) for s in rows]


@router.post(
    "/suppliers",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("purchasing.supplier.manage"))],
)
async def create_supplier(
    payload: SupplierCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierResponse:
    supplier = await SupplierService(db, audit).create(
        organization_id=context.require_organization(),
        name=payload.name,
        tax_number=payload.tax_number,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return SupplierResponse.model_validate(supplier)


@router.patch(
    "/suppliers/{supplier_id}",
    response_model=SupplierResponse,
    dependencies=[Depends(require_permissions("purchasing.supplier.manage"))],
)
async def update_supplier(
    supplier_id: uuid.UUID,
    payload: SupplierUpdateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierResponse:
    supplier = await SupplierService(db, audit).update(
        organization_id=context.require_organization(),
        supplier_id=supplier_id,
        changes=payload.model_dump(exclude_unset=True),
        actor_user_id=context.user_id,
    )
    await db.commit()
    return SupplierResponse.model_validate(supplier)


@router.get(
    "/suppliers/{supplier_id}/balance",
    response_model=SupplierBalanceResponse,
    dependencies=[Depends(require_permissions("purchasing.supplier.read"))],
)
async def supplier_balance(
    supplier_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierBalanceResponse:
    """What is owed to this supplier, and what has been paid ahead."""
    balance = await PayablesService(db, audit).supplier_balance(
        organization_id=context.require_organization(), supplier_id=supplier_id
    )
    return SupplierBalanceResponse(**asdict(balance))


# ------------------------------------------------------------------- requests


@router.get(
    "/requests",
    response_model=list[PurchaseRequestResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def list_requests(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    status_filter: list[RequestStatus] | None = Query(default=None, alias="status"),
) -> list[PurchaseRequestResponse]:
    rows = await PurchaseRequestService(db, audit).list_requests(
        organization_id=context.require_organization(),
        statuses=list(status_filter) if status_filter else None,
    )
    return [PurchaseRequestResponse.model_validate(r) for r in rows]


@router.get(
    "/requests/{request_id}",
    response_model=PurchaseRequestDetail,
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def get_request(
    request_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PurchaseRequestDetail:
    organization_id = context.require_organization()
    service = PurchaseRequestService(db, audit)
    request = await service.get(organization_id=organization_id, request_id=request_id)
    lines = await service.lines(organization_id=organization_id, request_id=request_id)
    return PurchaseRequestDetail(
        **PurchaseRequestResponse.model_validate(request).model_dump(),
        lines=[RequestLineResponse.model_validate(line, from_attributes=True) for line in lines],
    )


@router.post(
    "/requests",
    response_model=PurchaseRequestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("purchasing.order.manage"))],
)
async def create_request(
    payload: RequestCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PurchaseRequestResponse:
    """Ask for something to be bought. Posts nothing."""
    request = await PurchaseRequestService(db, audit).create(
        organization_id=context.require_organization(),
        lines=[
            RequestLineInput(
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
                estimated_unit_price=line.estimated_unit_price,
            )
            for line in payload.lines
        ],
        reason=payload.reason,
        needed_by=payload.needed_by,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return PurchaseRequestResponse.model_validate(request)


@router.post(
    "/requests/{request_id}/status",
    response_model=PurchaseRequestResponse,
    dependencies=[
        Depends(require_permissions("purchasing.order.read")),
    ],
)
async def set_request_status(
    request_id: uuid.UUID,
    payload: RequestStatusRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    duties_cannot_be_separated: DutySeparation,
) -> PurchaseRequestResponse:
    """Submit a request, or decide it.

    Submitting needs `purchasing.order.manage`; approving or rejecting needs
    `purchasing.order.approve`. Checked here rather than by one dependency
    because the two are a segregation-of-duties pair and the endpoint serves
    both sides of it.
    """
    if payload.status is RequestStatus.SUBMITTED:
        context.require("purchasing.order.manage")
    elif payload.status in {RequestStatus.APPROVED, RequestStatus.REJECTED}:
        context.require("purchasing.order.approve")
    else:
        context.require("purchasing.order.manage")

    request = await PurchaseRequestService(db, audit).set_status(
        organization_id=context.require_organization(),
        request_id=request_id,
        status=payload.status,
        note=payload.note,
        actor_user_id=context.user_id,
        duties_cannot_be_separated=duties_cannot_be_separated,
        actor_is_owner=context.has_any("organization.manage"),
    )
    await db.commit()
    return PurchaseRequestResponse.model_validate(request)


# --------------------------------------------------------------------- orders


@router.get(
    "/orders",
    response_model=list[PurchaseOrderResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def list_orders(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    supplier_id: uuid.UUID | None = Query(default=None),
    status_filter: list[OrderStatus] | None = Query(default=None, alias="status"),
) -> list[PurchaseOrderResponse]:
    organization_id = context.require_organization()
    rows = await PurchaseOrderService(db, audit).list_orders(
        organization_id=organization_id,
        supplier_id=supplier_id,
        statuses=list(status_filter) if status_filter else None,
    )
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return [_order_response(o, names) for o in rows]


@router.get(
    "/orders/{order_id}",
    response_model=PurchaseOrderDetail,
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def get_order(
    order_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PurchaseOrderDetail:
    organization_id = context.require_organization()
    orders = PurchaseOrderService(db, audit)
    order = await orders.get(organization_id=organization_id, order_id=order_id)
    lines = await orders.lines(organization_id=organization_id, order_id=order_id)
    outstanding = await ReceivingService(db, audit).outstanding(
        organization_id=organization_id, order_id=order_id
    )
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return PurchaseOrderDetail(
        **_order_response(order, names).model_dump(),
        lines=[OrderLineResponse.model_validate(line, from_attributes=True) for line in lines],
        outstanding=[OutstandingLineResponse(**asdict(row)) for row in outstanding],
    )


@router.post(
    "/orders",
    response_model=PurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("purchasing.order.manage"))],
)
async def create_order(
    payload: OrderCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PurchaseOrderResponse:
    """Raise a draft order. **Posts nothing** — it is a promise to buy."""
    organization_id = context.require_organization()
    order = await PurchaseOrderService(db, audit).create(
        organization_id=organization_id,
        supplier_id=payload.supplier_id,
        lines=[
            OrderLineInput(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                unit=line.unit,
                item_id=line.item_id,
            )
            for line in payload.lines
        ],
        ordered_on=payload.ordered_on,
        expected_on=payload.expected_on,
        request_id=payload.request_id,
        currency=payload.currency,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    await db.commit()
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return _order_response(order, names)


@router.post(
    "/orders/{order_id}/status",
    response_model=PurchaseOrderResponse,
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def set_order_status(
    order_id: uuid.UUID,
    payload: OrderStatusRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    duties_cannot_be_separated: DutySeparation,
) -> PurchaseOrderResponse:
    """Approve, send, cancel or close an order. **Posts nothing.**"""
    if payload.status is OrderStatus.APPROVED:
        context.require("purchasing.order.approve")
    else:
        context.require("purchasing.order.manage")

    organization_id = context.require_organization()
    order = await PurchaseOrderService(db, audit).set_status(
        organization_id=organization_id,
        order_id=order_id,
        status=payload.status,
        reason=payload.reason,
        actor_user_id=context.user_id,
        duties_cannot_be_separated=duties_cannot_be_separated,
        actor_is_owner=context.has_any("organization.manage"),
    )
    await db.commit()
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return _order_response(order, names)


# ------------------------------------------------------------------- receipts


@router.get(
    "/receipts",
    response_model=list[GoodsReceiptResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def list_receipts(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    supplier_id: uuid.UUID | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
) -> list[GoodsReceiptResponse]:
    rows = await ReceivingService(db, audit).list_receipts(
        organization_id=context.require_organization(),
        supplier_id=supplier_id,
        order_id=order_id,
    )
    return [GoodsReceiptResponse.model_validate(r) for r in rows]


@router.get(
    "/receipts/{receipt_id}",
    response_model=GoodsReceiptDetail,
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def get_receipt(
    receipt_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> GoodsReceiptDetail:
    organization_id = context.require_organization()
    service = ReceivingService(db, audit)
    receipt = await service.get(organization_id=organization_id, receipt_id=receipt_id)
    lines = await service.lines(organization_id=organization_id, receipt_id=receipt_id)
    return GoodsReceiptDetail(
        **GoodsReceiptResponse.model_validate(receipt).model_dump(),
        lines=[ReceiptLineResponse.model_validate(line, from_attributes=True) for line in lines],
    )


@router.post(
    "/receipts",
    response_model=GoodsReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.receipt.record"))],
)
async def receive_goods(
    payload: ReceiptCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> GoodsReceiptResponse:
    """Record what arrived. **Posts nothing** — see the module docstring.

    Behind `inventory.receipt.record` rather than a purchasing permission:
    the person who takes a delivery at the door is the one who runs the
    stock, and that is the permission the inventory-manager role already
    carries.
    """
    receipt = await ReceivingService(db, audit).receive(
        organization_id=context.require_organization(),
        supplier_id=payload.supplier_id,
        lines=[
            ReceiptLineInput(
                description=line.description,
                quantity=line.quantity,
                order_line_id=line.order_line_id,
                unit=line.unit,
                rejected_quantity=line.rejected_quantity,
                rejected_reason=line.rejected_reason,
                item_id=line.item_id,
                location_id=line.location_id,
                batch_code=line.batch_code,
                batch_expires_on=line.batch_expires_on,
            )
            for line in payload.lines
        ],
        order_id=payload.order_id,
        received_on=payload.received_on,
        delivery_note=payload.delivery_note,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return GoodsReceiptResponse.model_validate(receipt)


@router.get(
    "/suppliers/{supplier_id}/uninvoiced",
    response_model=list[UninvoicedLineResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def uninvoiced_lines(
    supplier_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[UninvoicedLineResponse]:
    """What has arrived from this supplier and nobody has billed for.

    The question the four-record shape exists to answer, and the one that
    catches a supplier invoicing the same delivery twice.
    """
    organization_id = context.require_organization()
    service = ReceivingService(db, audit)
    lines = await service.uninvoiced_receipt_lines(
        organization_id=organization_id, supplier_id=supplier_id
    )
    receipts = {
        r.id: r
        for r in await service.list_receipts(
            organization_id=organization_id, supplier_id=supplier_id
        )
    }
    rows: list[UninvoicedLineResponse] = []
    for line in lines:
        receipt = receipts.get(line.receipt_id)
        if receipt is None:
            continue
        rows.append(
            UninvoicedLineResponse(
                id=line.id,
                receipt_id=line.receipt_id,
                receipt_code=receipt.code,
                received_on=receipt.received_on,
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
            )
        )
    return rows


# ---------------------------------------------------------------------- bills


async def _bill_response(
    invoice: SupplierInvoice,
    *,
    db: DbSession,
    audit: AuditDep,
    names: dict[uuid.UUID, str],
) -> SupplierInvoiceResponse:
    balance = await PayablesService(db, audit).bill_balance(
        organization_id=invoice.organization_id, invoice_id=invoice.id
    )
    return SupplierInvoiceResponse(
        id=invoice.id,
        code=invoice.code,
        supplier_id=invoice.supplier_id,
        supplier_name=names.get(invoice.supplier_id, "—"),
        order_id=invoice.order_id,
        supplier_reference=invoice.supplier_reference,
        kind=invoice.kind,
        status=invoice.status,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        total=invoice.total,
        paid=balance.paid,
        remaining=balance.remaining,
        registered_at=invoice.registered_at,
        void_reason=invoice.void_reason,
        notes=invoice.notes,
    )


@router.get(
    "/bills",
    response_model=list[SupplierInvoiceResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def list_bills(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    supplier_id: uuid.UUID | None = Query(default=None),
    status_filter: list[BillStatus] | None = Query(default=None, alias="status"),
) -> list[SupplierInvoiceResponse]:
    organization_id = context.require_organization()
    rows = await PayablesService(db, audit).list_bills(
        organization_id=organization_id,
        supplier_id=supplier_id,
        statuses=list(status_filter) if status_filter else None,
    )
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return [await _bill_response(b, db=db, audit=audit, names=names) for b in rows]


@router.get(
    "/bills/{invoice_id}",
    response_model=SupplierInvoiceDetail,
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def get_bill(
    invoice_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierInvoiceDetail:
    organization_id = context.require_organization()
    service = PayablesService(db, audit)
    invoice = await service.get_bill(organization_id=organization_id, invoice_id=invoice_id)
    lines = await service.bill_lines(organization_id=organization_id, invoice_id=invoice_id)
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    base = await _bill_response(invoice, db=db, audit=audit, names=names)
    return SupplierInvoiceDetail(
        **base.model_dump(),
        lines=[BillLineResponse.model_validate(line, from_attributes=True) for line in lines],
    )


@router.post(
    "/bills",
    response_model=SupplierInvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("purchasing.bill.manage"))],
)
async def create_bill(
    payload: BillCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierInvoiceResponse:
    """Enter a supplier's invoice as a draft. **Posts nothing yet.**"""
    organization_id = context.require_organization()
    invoice = await PayablesService(db, audit).create_bill(
        organization_id=organization_id,
        supplier_id=payload.supplier_id,
        kind=payload.kind,
        lines=[
            BillLineInput(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                unit=line.unit,
                receipt_line_id=line.receipt_line_id,
            )
            for line in payload.lines
        ],
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        order_id=payload.order_id,
        supplier_reference=payload.supplier_reference,
        currency=payload.currency,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    await db.commit()
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return await _bill_response(invoice, db=db, audit=audit, names=names)


@router.post(
    "/bills/{invoice_id}/register",
    response_model=SupplierInvoiceResponse,
    dependencies=[Depends(require_permissions("purchasing.bill.manage"))],
)
async def register_bill(
    invoice_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierInvoiceResponse:
    """Accept the bill into the books. The payable appears here."""
    organization_id = context.require_organization()
    invoice = await PayablesService(db, audit).register_bill(
        organization_id=organization_id,
        invoice_id=invoice_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return await _bill_response(invoice, db=db, audit=audit, names=names)


@router.post(
    "/bills/{invoice_id}/void",
    response_model=SupplierInvoiceResponse,
    dependencies=[Depends(require_permissions("purchasing.bill.manage"))],
)
async def void_bill(
    invoice_id: uuid.UUID,
    payload: VoidBillRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierInvoiceResponse:
    """Reverse a registered bill. Both entries stay on the record."""
    organization_id = context.require_organization()
    invoice = await PayablesService(db, audit).void_bill(
        organization_id=organization_id,
        invoice_id=invoice_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return await _bill_response(invoice, db=db, audit=audit, names=names)


# ------------------------------------------------------------------- payments


async def _payment_response(
    payment: SupplierPayment,
    *,
    db: DbSession,
    audit: AuditDep,
    names: dict[uuid.UUID, str],
) -> SupplierPaymentResponse:
    service = PayablesService(db, audit)
    unapplied = await service.unallocated_of(
        organization_id=payment.organization_id, payment_id=payment.id
    )
    return SupplierPaymentResponse(
        id=payment.id,
        code=payment.code,
        supplier_id=payment.supplier_id,
        supplier_name=names.get(payment.supplier_id, "—"),
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        status=payment.status,
        paid_on=payment.paid_on,
        reference=payment.reference,
        notes=payment.notes,
        unapplied=unapplied,
    )


@router.get(
    "/payments",
    response_model=list[SupplierPaymentResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def list_payments(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    supplier_id: uuid.UUID | None = Query(default=None),
) -> list[SupplierPaymentResponse]:
    organization_id = context.require_organization()
    rows = await PayablesService(db, audit).list_payments(
        organization_id=organization_id, supplier_id=supplier_id
    )
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return [await _payment_response(p, db=db, audit=audit, names=names) for p in rows]


@router.post(
    "/payments",
    response_model=SupplierPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("purchasing.payment.record"))],
)
async def record_payment(
    payload: PaymentCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SupplierPaymentResponse:
    """Pay a supplier.

    With a bill, the payment settles it. Without one it is an **advance**,
    held until a bill arrives to apply it to — which is how this clinic
    actually buys.

    A **cash** payment also moves the drawer, so it goes through the cash
    module's composite service: a payment that reached the ledger and not
    the till would leave the evening count short by exactly the amount
    nobody could explain.
    """
    organization_id = context.require_organization()
    if payload.method is SupplierPaymentMethod.CASH:
        if payload.cash_session_id is None:
            raise ValidationFailed(
                "Paying a supplier in cash needs an open till, so the drawer moves with the ledger",
                details={"field": "cash_session_id"},
            )
        paid = await SupplierCashSettlementService(db, audit).pay_from_till(
            organization_id=organization_id,
            session_id=payload.cash_session_id,
            supplier_id=payload.supplier_id,
            amount=payload.amount,
            paid_on=payload.paid_on,
            invoice_id=payload.invoice_id,
            reference=payload.reference,
            notes=payload.notes,
            actor_user_id=context.user_id,
        )
        payment = await PayablesService(db, audit).get_payment(
            organization_id=organization_id, payment_id=paid.payment_id
        )
    else:
        if payload.cash_session_id is not None:
            raise ValidationFailed(
                "A till session belongs only to a cash payment",
                details={"field": "cash_session_id"},
            )
        payment = await PayablesService(db, audit).record_payment(
            organization_id=organization_id,
            supplier_id=payload.supplier_id,
            amount=payload.amount,
            method=payload.method,
            paid_on=payload.paid_on,
            invoice_id=payload.invoice_id,
            reference=payload.reference,
            notes=payload.notes,
            currency=payload.currency,
            actor_user_id=context.user_id,
        )
    await db.commit()
    names = await _supplier_names(db=db, audit=audit, organization_id=organization_id)
    return await _payment_response(payment, db=db, audit=audit, names=names)


@router.post(
    "/payments/{payment_id}/apply",
    response_model=AllocationResponse,
    dependencies=[Depends(require_permissions("purchasing.payment.record"))],
)
async def apply_advance(
    payment_id: uuid.UUID,
    payload: ApplyAdvanceRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AllocationResponse:
    """Apply money already paid to a bill that has now arrived."""
    allocation = await PayablesService(db, audit).apply_advance(
        organization_id=context.require_organization(),
        payment_id=payment_id,
        invoice_id=payload.invoice_id,
        amount=payload.amount,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AllocationResponse.model_validate(allocation, from_attributes=True)


@router.post(
    "/allocations/{allocation_id}/reverse",
    response_model=AllocationResponse,
    dependencies=[Depends(require_permissions("purchasing.payment.record"))],
)
async def reverse_allocation(
    allocation_id: uuid.UUID,
    payload: ReverseAllocationRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AllocationResponse:
    """Undo an application, marking it rather than deleting it."""
    allocation = await PayablesService(db, audit).reverse_allocation(
        organization_id=context.require_organization(),
        allocation_id=allocation_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AllocationResponse.model_validate(allocation, from_attributes=True)


@router.get(
    "/payables/ageing",
    response_model=list[PayablesAgeingRowResponse],
    dependencies=[Depends(require_permissions("purchasing.order.read"))],
)
async def payables_ageing(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    as_of: date | None = Query(default=None),
) -> list[PayablesAgeingRowResponse]:
    """What is owed, by how overdue it is. Derived, never stored."""
    rows = await PayablesService(db, audit).ageing(
        organization_id=context.require_organization(), as_of=as_of
    )
    return [PayablesAgeingRowResponse(**asdict(row)) for row in rows]
