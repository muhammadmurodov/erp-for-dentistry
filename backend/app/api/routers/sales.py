"""Sales endpoints: catalogue, customers, invoices, payments, receivables."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.money import ZERO, quantize
from app.modules.sales.enums import InvoiceStatus, PaymentStatus
from app.modules.sales.models import Invoice, Payment
from app.modules.sales.schemas import (
    AgeingBucket,
    AllocationRequest,
    AllocationResponse,
    CustomerAgeing,
    CustomerBalanceResponse,
    CustomerCreateRequest,
    CustomerResponse,
    InvoiceCreateRequest,
    InvoiceDetail,
    InvoiceLineResponse,
    InvoiceResponse,
    IssueInvoiceRequest,
    PaymentCreateRequest,
    PaymentDetail,
    PaymentResponse,
    ReceivablesAgeing,
    RefundRequest,
    RemoveAllocationRequest,
    ServiceCreateRequest,
    ServicePriceRequest,
    ServicePriceResponse,
    ServiceResponse,
    ServiceWithPrice,
    VoidInvoiceRequest,
)
from app.modules.sales.services.billing import BillingService, InvoiceLineInput
from app.modules.sales.services.catalogue import CatalogueService
from app.modules.sales.services.receivables import ReceivablesService

router = APIRouter(prefix="/sales", tags=["sales"])


# Amounts are stored at NUMERIC(20, 4) so that intermediate values have
# headroom. The API presents them at the currency's own scale, so a som
# figure reads as 300000.00 rather than 300000.0000.


def _quantized_invoice(invoice: Invoice, allocated: Decimal) -> InvoiceResponse:
    row = InvoiceResponse.model_validate(invoice)
    row.subtotal = quantize(invoice.subtotal, invoice.currency)
    row.tax_total = quantize(invoice.tax_total, invoice.currency)
    row.total = quantize(invoice.total, invoice.currency)
    row.allocated = quantize(allocated, invoice.currency)
    row.outstanding = quantize(invoice.total - allocated, invoice.currency)
    return row


def _quantized_line(line, currency: str) -> InvoiceLineResponse:
    row = InvoiceLineResponse.model_validate(line)
    row.unit_price = quantize(line.unit_price, currency)
    row.line_total = quantize(line.line_total, currency)
    # Quantity is a count, not money: normalise it for display without
    # forcing it to a currency scale.
    row.quantity = line.quantity.normalize()
    return row


def _quantized_payment(payment: Payment, allocated: Decimal) -> PaymentResponse:
    row = PaymentResponse.model_validate(payment)
    row.amount = quantize(payment.amount, payment.currency)
    row.allocated = quantize(allocated, payment.currency)
    row.advance = quantize(payment.amount - allocated, payment.currency)
    return row


def _quantized_allocation(allocation, currency: str) -> AllocationResponse:
    row = AllocationResponse.model_validate(allocation)
    row.amount = quantize(allocation.amount, currency)
    return row


# -------------------------------------------------------------- customers


@router.get(
    "/customers",
    response_model=list[CustomerResponse],
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def list_customers(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    search: str | None = Query(default=None, max_length=64),
    active_only: bool = Query(default=True),
) -> list[CustomerResponse]:
    """People and companies that can be billed.

    This is the *billing* identity. Clinical records live behind
    `clinic.clinical.read` and are not reachable from here.
    """
    customers = await CatalogueService(db, audit).list_customers(
        organization_id=context.require_organization(),
        search=search,
        active_only=active_only,
    )
    return [CustomerResponse.model_validate(c) for c in customers]


@router.post(
    "/customers",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.invoice.manage"))],
)
async def create_customer(
    payload: CustomerCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> CustomerResponse:
    customer = await CatalogueService(db, audit).create_customer(
        organization_id=context.require_organization(),
        **payload.model_dump(),
    )
    await db.commit()
    return CustomerResponse.model_validate(customer)


@router.get(
    "/customers/{customer_id}/balance",
    response_model=CustomerBalanceResponse,
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def customer_balance(
    customer_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> CustomerBalanceResponse:
    """What this customer owes, and what they have paid in advance."""
    organization_id = context.require_organization()
    await CatalogueService(db, audit).get_customer(
        organization_id=organization_id, customer_id=customer_id
    )
    balance = await BillingService(db, audit).customer_balance(
        organization_id=organization_id, customer_id=customer_id
    )
    return CustomerBalanceResponse(**asdict(balance))


# --------------------------------------------------------------- services


@router.get(
    "/services",
    response_model=list[ServiceWithPrice],
    dependencies=[Depends(require_permissions("sales.service.read"))],
)
async def list_services(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    search: str | None = Query(default=None, max_length=64),
    active_only: bool = Query(default=True),
    on: date | None = Query(default=None, description="Price as at this date"),
) -> list[ServiceWithPrice]:
    """The service catalogue with each item's price in force."""
    organization_id = context.require_organization()
    catalogue = CatalogueService(db, audit)
    services = await catalogue.list_services(
        organization_id=organization_id, search=search, active_only=active_only
    )
    as_of = on or date.today()
    result: list[ServiceWithPrice] = []
    for service in services:
        price = await catalogue.price_on(
            organization_id=organization_id, service_id=service.id, on=as_of
        )
        result.append(
            ServiceWithPrice(
                **ServiceResponse.model_validate(service).model_dump(),
                # Stored at NUMERIC(20,4) for intermediate headroom; shown at
                # the currency's own scale.
                current_price=(quantize(price.amount, price.currency) if price else None),
                price_effective_from=price.effective_from if price else None,
            )
        )
    return result


@router.post(
    "/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.service.manage"))],
)
async def create_service(
    payload: ServiceCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> ServiceResponse:
    """Add a service, optionally with its opening price."""
    data = payload.model_dump()
    price = data.pop("price", None)
    effective_from = data.pop("effective_from", None)
    code = data.pop("code")
    name = data.pop("name")
    category = data.pop("category")

    service = await CatalogueService(db, audit).create_service(
        organization_id=context.require_organization(),
        code=code,
        name=name,
        category=category,
        price=price,
        effective_from=effective_from,
        **data,
    )
    await db.commit()
    return ServiceResponse.model_validate(service)


@router.get(
    "/services/{service_id}/prices",
    response_model=list[ServicePriceResponse],
    dependencies=[Depends(require_permissions("sales.service.read"))],
)
async def list_service_prices(
    service_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> list[ServicePriceResponse]:
    """Price history. Prices are versioned, never overwritten."""
    organization_id = context.require_organization()
    catalogue = CatalogueService(db, audit)
    await catalogue.get_service(organization_id=organization_id, service_id=service_id)
    prices = await catalogue.list_prices(organization_id=organization_id, service_id=service_id)
    return [
        ServicePriceResponse.model_validate(
            {
                **{c: getattr(p, c) for c in ("id", "currency", "effective_from", "effective_to")},
                "amount": quantize(p.amount, p.currency),
            }
        )
        for p in prices
    ]


@router.post(
    "/services/{service_id}/prices",
    response_model=ServicePriceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.service.manage"))],
)
async def set_service_price(
    service_id: uuid.UUID,
    payload: ServicePriceRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> ServicePriceResponse:
    """Start a new price from a date. The previous one is closed, not erased."""
    price = await CatalogueService(db, audit).set_price(
        organization_id=context.require_organization(),
        service_id=service_id,
        amount=payload.amount,
        effective_from=payload.effective_from,
        currency=payload.currency,
    )
    await db.commit()
    return ServicePriceResponse.model_validate(price)


# --------------------------------------------------------------- invoices


async def _invoice_detail(
    db: DbSession, audit: AuditDep, organization_id: uuid.UUID, invoice: Invoice
) -> InvoiceDetail:
    billing = BillingService(db, audit)
    lines = await billing.list_lines(organization_id=organization_id, invoice_id=invoice.id)
    allocated = await billing.allocated_to_invoice(
        organization_id=organization_id, invoice_id=invoice.id
    )
    customer = await CatalogueService(db, audit).get_customer(
        organization_id=organization_id, customer_id=invoice.customer_id
    )
    base = _quantized_invoice(invoice, allocated)
    return InvoiceDetail(
        **base.model_dump(),
        lines=[_quantized_line(line, invoice.currency) for line in lines],
        customer_name=customer.display_name,
    )


@router.get(
    "/invoices",
    response_model=list[InvoiceResponse],
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def list_invoices(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    invoice_status: InvoiceStatus | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[InvoiceResponse]:
    from sqlalchemy import select

    organization_id = context.require_organization()
    stmt = select(Invoice).where(Invoice.organization_id == organization_id)
    if invoice_status is not None:
        stmt = stmt.where(Invoice.status == invoice_status)
    if customer_id is not None:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    stmt = stmt.order_by(Invoice.issue_date.desc(), Invoice.number.desc()).limit(limit)

    billing = BillingService(db, audit)
    result: list[InvoiceResponse] = []
    for invoice in (await db.execute(stmt)).scalars():
        allocated = await billing.allocated_to_invoice(
            organization_id=organization_id, invoice_id=invoice.id
        )
        result.append(_quantized_invoice(invoice, allocated))
    return result


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceDetail,
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def get_invoice(
    invoice_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> InvoiceDetail:
    organization_id = context.require_organization()
    invoice = await BillingService(db, audit).get_invoice(
        organization_id=organization_id, invoice_id=invoice_id
    )
    return await _invoice_detail(db, audit, organization_id, invoice)


@router.post(
    "/invoices",
    response_model=InvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.invoice.manage"))],
)
async def create_invoice(
    payload: InvoiceCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> InvoiceDetail:
    """Prepare a draft invoice. A draft has no ledger effect."""
    organization_id = context.require_organization()
    invoice = await BillingService(db, audit).create_invoice(
        organization_id=organization_id,
        customer_id=payload.customer_id,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        currency=payload.currency,
        notes=payload.notes,
        lines=[
            InvoiceLineInput(
                description=line.description,
                quantity=line.quantity,
                service_id=line.service_id,
                unit_price=line.unit_price,
            )
            for line in payload.lines
        ],
    )
    await db.commit()
    return await _invoice_detail(db, audit, organization_id, invoice)


@router.post(
    "/invoices/{invoice_id}/issue",
    response_model=InvoiceDetail,
    dependencies=[Depends(require_permissions("sales.invoice.manage"))],
)
async def issue_invoice(
    invoice_id: uuid.UUID,
    payload: IssueInvoiceRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> InvoiceDetail:
    """Issue the invoice: recognise revenue and create the receivable.

    The tax treatment must be stated, not defaulted — the clinic's receipts
    and its bank statement disagree about VAT, and the system will not choose
    on an accountant's behalf (OPEN_QUESTIONS A5).
    """
    organization_id = context.require_organization()
    invoice = await BillingService(db, audit).issue_invoice(
        organization_id=organization_id,
        invoice_id=invoice_id,
        tax_treatment=payload.tax_treatment,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _invoice_detail(db, audit, organization_id, invoice)


@router.post(
    "/invoices/{invoice_id}/void",
    response_model=InvoiceDetail,
    dependencies=[Depends(require_permissions("sales.refund.approve"))],
)
async def void_invoice(
    invoice_id: uuid.UUID,
    payload: VoidInvoiceRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> InvoiceDetail:
    """Reverse an issued invoice. Requires approval authority."""
    organization_id = context.require_organization()
    invoice = await BillingService(db, audit).void_invoice(
        organization_id=organization_id,
        invoice_id=invoice_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _invoice_detail(db, audit, organization_id, invoice)


# --------------------------------------------------------------- payments


async def _payment_detail(
    db: DbSession, audit: AuditDep, organization_id: uuid.UUID, payment: Payment
) -> PaymentDetail:
    billing = BillingService(db, audit)
    allocations = await billing.list_allocations(
        organization_id=organization_id, payment_id=payment.id
    )
    allocated: Decimal = sum((a.amount for a in allocations), ZERO)
    customer = await CatalogueService(db, audit).get_customer(
        organization_id=organization_id, customer_id=payment.customer_id
    )
    base = _quantized_payment(payment, allocated)
    return PaymentDetail(
        **base.model_dump(),
        allocations=[_quantized_allocation(a, payment.currency) for a in allocations],
        customer_name=customer.display_name,
    )


@router.get(
    "/payments",
    response_model=list[PaymentResponse],
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def list_payments(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    customer_id: uuid.UUID | None = Query(default=None),
    payment_status: PaymentStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PaymentResponse]:
    from sqlalchemy import select

    organization_id = context.require_organization()
    stmt = select(Payment).where(Payment.organization_id == organization_id)
    if customer_id is not None:
        stmt = stmt.where(Payment.customer_id == customer_id)
    if payment_status is not None:
        stmt = stmt.where(Payment.status == payment_status)
    stmt = stmt.order_by(Payment.received_on.desc(), Payment.number.desc()).limit(limit)

    billing = BillingService(db, audit)
    result: list[PaymentResponse] = []
    for payment in (await db.execute(stmt)).scalars():
        allocated = await billing.allocated_from_payment(
            organization_id=organization_id, payment_id=payment.id
        )
        result.append(_quantized_payment(payment, allocated))
    return result


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentDetail,
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def get_payment(
    payment_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> PaymentDetail:
    organization_id = context.require_organization()
    payment = await BillingService(db, audit).get_payment(
        organization_id=organization_id, payment_id=payment_id
    )
    return await _payment_detail(db, audit, organization_id, payment)


@router.post(
    "/payments",
    response_model=PaymentDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.payment.record"))],
)
async def record_payment(
    payload: PaymentCreateRequest, context: OrgCtx, db: DbSession, audit: AuditDep
) -> PaymentDetail:
    """Record money received and post it.

    Whatever is not allocated to an invoice is an advance — a liability, not
    revenue.
    """
    organization_id = context.require_organization()
    payment = await BillingService(db, audit).record_payment(
        organization_id=organization_id,
        customer_id=payload.customer_id,
        amount=payload.amount,
        method=payload.method,
        received_on=payload.received_on,
        currency=payload.currency,
        reference=payload.reference,
        notes=payload.notes,
        allocate_to=[(a.invoice_id, a.amount) for a in payload.allocations],
        auto_allocate=payload.auto_allocate,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _payment_detail(db, audit, organization_id, payment)


@router.post(
    "/payments/{payment_id}/allocate",
    response_model=PaymentDetail,
    dependencies=[Depends(require_permissions("sales.payment.record"))],
)
async def allocate_payment(
    payment_id: uuid.UUID,
    payload: list[AllocationRequest],
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PaymentDetail:
    """Apply an existing advance to one or more invoices."""
    organization_id = context.require_organization()
    billing = BillingService(db, audit)
    for item in payload:
        await billing.allocate(
            organization_id=organization_id,
            payment_id=payment_id,
            invoice_id=item.invoice_id,
            amount=item.amount,
            actor_user_id=context.user_id,
        )
    await db.commit()
    payment = await billing.get_payment(organization_id=organization_id, payment_id=payment_id)
    return await _payment_detail(db, audit, organization_id, payment)


@router.post(
    "/payments/{payment_id}/refund",
    response_model=PaymentDetail,
    dependencies=[Depends(require_permissions("sales.refund.approve"))],
)
async def refund_payment(
    payment_id: uuid.UUID,
    payload: RefundRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PaymentDetail:
    """Reverse a payment in full. Requires approval authority, not just the
    ability to take payments — a cashier cannot refund their own takings."""
    organization_id = context.require_organization()
    payment = await BillingService(db, audit).refund_payment(
        organization_id=organization_id,
        payment_id=payment_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return await _payment_detail(db, audit, organization_id, payment)


@router.post(
    "/allocations/{allocation_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permissions("sales.payment.record"))],
)
async def remove_allocation(
    allocation_id: uuid.UUID,
    payload: RemoveAllocationRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> None:
    """Unapply money from an invoice. Audited with its reason."""
    await BillingService(db, audit).remove_allocation(
        organization_id=context.require_organization(),
        allocation_id=allocation_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()


# ------------------------------------------------------------ receivables


@router.get(
    "/receivables/ageing",
    response_model=ReceivablesAgeing,
    dependencies=[Depends(require_permissions("reporting.financial.read"))],
)
async def receivables_ageing(
    context: OrgCtx,
    db: DbSession,
    as_of: date | None = Query(default=None),
) -> ReceivablesAgeing:
    """Outstanding receivables, bucketed by how overdue they are.

    Ageing runs from the due date where one is set, because an invoice is not
    overdue before it is due.
    """
    report = await ReceivablesService(db).ageing(
        organization_id=context.require_organization(), as_of=as_of
    )
    return ReceivablesAgeing(
        as_of=report.as_of,
        currency=report.currency,
        total_outstanding=report.total_outstanding,
        bucket_totals=[AgeingBucket(**asdict(b)) for b in report.bucket_totals],
        customers=[
            CustomerAgeing(
                customer_id=row.customer_id,
                customer_code=row.customer_code,
                customer_name=row.customer_name,
                total_outstanding=row.total_outstanding,
                buckets=[AgeingBucket(**asdict(b)) for b in row.buckets],
            )
            for row in report.customers
        ],
    )
