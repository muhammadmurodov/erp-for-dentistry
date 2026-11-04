"""Suppliers, purchase requests and purchase orders.

Nothing in this file posts to the ledger, and that is the file's theme. A
request is somebody asking; an order is a promise to buy. Neither moves an
asset or creates a liability, so neither has any business in the books —
posting at order time is how a cancelled order leaves a phantom payable.

What they do carry is **who decided**. An order commits the clinic to paying
somebody, and `purchasing.order.manage` / `purchasing.order.approve` are a
segregation-of-duties pair for exactly that reason. Where a clinic of three
cannot staff both sides, the declaration under ADR-041 relaxes *who* may
approve and the approval is recorded as self-approved — never silently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.money import ZERO, quantize, sum_amounts
from app.core.permissions import one_person_may_do_and_approve
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder, changed_fields
from app.modules.purchasing.enums import (
    CLOSED_ORDER_STATUSES,
    OrderStatus,
    RequestStatus,
)
from app.modules.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequest,
    PurchaseRequestLine,
    Supplier,
)

#: Which request states may follow which.
REQUEST_TRANSITIONS: dict[RequestStatus, frozenset[RequestStatus]] = {
    RequestStatus.DRAFT: frozenset({RequestStatus.SUBMITTED, RequestStatus.CANCELLED}),
    RequestStatus.SUBMITTED: frozenset(
        {RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.CANCELLED}
    ),
    # An approved request that nobody ordered against can still be dropped;
    # one that produced an order cannot, because the order refers to it.
    RequestStatus.APPROVED: frozenset({RequestStatus.ORDERED, RequestStatus.CANCELLED}),
    RequestStatus.REJECTED: frozenset(),
    RequestStatus.ORDERED: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
}

#: Which order states may follow which, for the moves a person makes.
#: Receiving moves an order on its own — see `ReceivingService`.
ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.APPROVED, OrderStatus.CANCELLED}),
    OrderStatus.APPROVED: frozenset({OrderStatus.SENT, OrderStatus.CANCELLED}),
    OrderStatus.SENT: frozenset({OrderStatus.CANCELLED, OrderStatus.CLOSED}),
    OrderStatus.PARTIALLY_RECEIVED: frozenset({OrderStatus.CLOSED}),
    OrderStatus.RECEIVED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RequestLineInput:
    description: str
    quantity: Decimal
    unit: str | None = None
    estimated_unit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderLineInput:
    description: str
    quantity: Decimal
    unit_price: Decimal
    unit: str | None = None
    #: The stocked material being ordered, when it is one. Carried so that
    #: the delivery can offer it without somebody re-typing it.
    item_id: uuid.UUID | None = None


class SupplierService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def list_suppliers(
        self,
        *,
        organization_id: uuid.UUID,
        include_inactive: bool = False,
        query: str | None = None,
    ) -> list[Supplier]:
        stmt = select(Supplier).where(Supplier.organization_id == organization_id)
        if not include_inactive:
            stmt = stmt.where(Supplier.is_active.is_(True))
        if query and query.strip():
            needle = f"%{query.strip().lower()}%"
            # Name, code and tax number. Not the telephone: a digit string
            # typed into a name box matching somebody's number is the defect
            # the patient search already fixed once.
            stmt = stmt.where(
                Supplier.name.ilike(needle)
                | Supplier.code.ilike(needle)
                | Supplier.tax_number.ilike(needle)
            )
        return list((await self._session.execute(stmt.order_by(Supplier.name))).scalars().all())

    async def get(self, *, organization_id: uuid.UUID, supplier_id: uuid.UUID) -> Supplier:
        stmt = select(Supplier).where(
            Supplier.organization_id == organization_id, Supplier.id == supplier_id
        )
        supplier = (await self._session.execute(stmt)).scalar_one_or_none()
        if supplier is None:
            raise NotFound("Supplier not found")
        return supplier

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        name: str,
        tax_number: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Supplier:
        name = name.strip()
        if not name:
            raise ValidationFailed("A supplier needs a name")

        tax_number = (tax_number or "").strip() or None
        if tax_number is not None:
            clash = (
                await self._session.execute(
                    select(Supplier).where(
                        Supplier.organization_id == organization_id,
                        Supplier.tax_number == tax_number,
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                # Warned, not refused in the database: two legal entities
                # genuinely sharing a STIR is impossible, but a mistyped one
                # is not, and the person entering it is better placed to say.
                raise Conflict(
                    f"{clash.name} ({clash.code}) already has that tax number",
                    details={"supplier_id": str(clash.id), "tax_number": tax_number},
                )

        supplier = Supplier(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="supplier",
                prefix="SUP-",
                padding=4,
            ),
            name=name,
            tax_number=tax_number,
            phone=(phone or "").strip() or None,
            email=(email or "").strip() or None,
            address=(address or "").strip() or None,
            notes=notes,
        )
        self._session.add(supplier)
        await self._session.flush()

        self._audit.record(
            events.SUPPLIER_CREATED,
            organization_id=organization_id,
            target_type="supplier",
            target_id=supplier.id,
            target_label=supplier.code,
            actor_user_id=actor_user_id,
            new_values={"name": name, "tax_number": tax_number},
        )
        return supplier

    async def update(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID,
        changes: dict[str, object],
        actor_user_id: uuid.UUID | None = None,
    ) -> Supplier:
        supplier = await self.get(organization_id=organization_id, supplier_id=supplier_id)
        editable = {"name", "tax_number", "phone", "email", "address", "notes", "is_active"}
        unknown = set(changes) - editable
        if unknown:
            raise ValidationFailed(
                f"These fields cannot be edited: {', '.join(sorted(unknown))}",
                details={"fields": sorted(unknown)},
            )
        before = {field: getattr(supplier, field) for field in editable}
        for field, value in changes.items():
            if isinstance(value, str):
                value = value.strip() or None
                if field == "name" and value is None:
                    raise ValidationFailed("A supplier needs a name")
            setattr(supplier, field, value)
        await self._session.flush()

        old_values, new_values = changed_fields(
            before, {field: getattr(supplier, field) for field in editable}
        )
        if old_values or new_values:
            self._audit.record(
                events.SUPPLIER_UPDATED,
                organization_id=organization_id,
                target_type="supplier",
                target_id=supplier.id,
                target_label=supplier.code,
                actor_user_id=actor_user_id,
                old_values=old_values,
                new_values=new_values,
            )
        return supplier


class PurchaseRequestService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def list_requests(
        self, *, organization_id: uuid.UUID, statuses: list[RequestStatus] | None = None
    ) -> list[PurchaseRequest]:
        stmt = select(PurchaseRequest).where(PurchaseRequest.organization_id == organization_id)
        if statuses:
            stmt = stmt.where(PurchaseRequest.status.in_(statuses))
        return list(
            (await self._session.execute(stmt.order_by(PurchaseRequest.created_at.desc())))
            .scalars()
            .all()
        )

    async def get(self, *, organization_id: uuid.UUID, request_id: uuid.UUID) -> PurchaseRequest:
        stmt = select(PurchaseRequest).where(
            PurchaseRequest.organization_id == organization_id,
            PurchaseRequest.id == request_id,
        )
        request = (await self._session.execute(stmt)).scalar_one_or_none()
        if request is None:
            raise NotFound("Purchase request not found")
        return request

    async def lines(
        self, *, organization_id: uuid.UUID, request_id: uuid.UUID
    ) -> list[PurchaseRequestLine]:
        stmt = (
            select(PurchaseRequestLine)
            .where(
                PurchaseRequestLine.organization_id == organization_id,
                PurchaseRequestLine.request_id == request_id,
            )
            .order_by(PurchaseRequestLine.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        lines: list[RequestLineInput],
        reason: str | None = None,
        needed_by: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> PurchaseRequest:
        if not lines:
            raise ValidationFailed("A purchase request needs at least one line")

        request = PurchaseRequest(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="purchase_request",
                prefix="PR-",
                padding=5,
            ),
            status=RequestStatus.DRAFT,
            requested_by_user_id=actor_user_id,
            needed_by=needed_by,
            reason=reason,
        )
        self._session.add(request)
        await self._session.flush()

        for line in lines:
            if line.quantity <= ZERO:
                raise ValidationFailed("A requested quantity must be positive")
            if not line.description.strip():
                raise ValidationFailed("Each line must say what is being asked for")
            self._session.add(
                PurchaseRequestLine(
                    organization_id=organization_id,
                    request_id=request.id,
                    description=line.description.strip(),
                    quantity=line.quantity,
                    unit=line.unit,
                    estimated_unit_price=line.estimated_unit_price,
                )
            )
        await self._session.flush()
        return request

    async def set_status(
        self,
        *,
        organization_id: uuid.UUID,
        request_id: uuid.UUID,
        status: RequestStatus,
        note: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        duties_cannot_be_separated: bool = False,
        actor_is_owner: bool = False,
    ) -> PurchaseRequest:
        """Submit a request, or decide it.

        Approving one's own request is refused unless the organization has
        declared it cannot separate duties, or the actor is the owner — the
        same rule the refunds and the till already follow (ADR-041). When it
        is allowed, `self_approved` records that it happened.
        """
        request = await self.get(organization_id=organization_id, request_id=request_id)
        if request.status is status:
            return request
        allowed = REQUEST_TRANSITIONS.get(request.status, frozenset())
        if status not in allowed:
            raise Conflict(
                f"A {request.status.value.lower()} request cannot become {status.value.lower()}",
                details={
                    "status": request.status.value,
                    "allowed": sorted(s.value for s in allowed),
                },
            )

        self_approved = False
        if status in {RequestStatus.APPROVED, RequestStatus.REJECTED}:
            same_person = (
                request.requested_by_user_id is not None
                and request.requested_by_user_id == actor_user_id
            )
            if same_person:
                if not one_person_may_do_and_approve(
                    duties_cannot_be_separated=duties_cannot_be_separated,
                    actor_is_owner=actor_is_owner,
                ):
                    raise PermissionDenied(
                        "The person who asked for this must not be the one who approves it",
                        details={"control": "segregation_of_duties"},
                    )
                self_approved = True
            request.decided_by_user_id = actor_user_id
            request.decided_at = datetime.now(UTC)
            request.decision_note = note
            request.self_approved = self_approved

        previous = request.status
        request.status = status
        await self._session.flush()

        self._audit.record(
            events.PURCHASE_REQUEST_SUBMITTED
            if status is RequestStatus.SUBMITTED
            else events.PURCHASE_REQUEST_DECIDED,
            organization_id=organization_id,
            target_type="purchase_request",
            target_id=request.id,
            target_label=request.code,
            business_event=f"purchase_request:{request.id}",
            actor_user_id=actor_user_id,
            reason=note,
            old_values={"status": previous.value},
            new_values={"status": status.value, "self_approved": self_approved},
        )
        return request


class PurchaseOrderService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._suppliers = SupplierService(session, audit)
        self._requests = PurchaseRequestService(session, audit)

    async def list_orders(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID | None = None,
        statuses: list[OrderStatus] | None = None,
    ) -> list[PurchaseOrder]:
        stmt = select(PurchaseOrder).where(PurchaseOrder.organization_id == organization_id)
        if supplier_id is not None:
            stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
        if statuses:
            stmt = stmt.where(PurchaseOrder.status.in_(statuses))
        return list(
            (await self._session.execute(stmt.order_by(PurchaseOrder.ordered_on.desc())))
            .scalars()
            .all()
        )

    async def get(self, *, organization_id: uuid.UUID, order_id: uuid.UUID) -> PurchaseOrder:
        stmt = select(PurchaseOrder).where(
            PurchaseOrder.organization_id == organization_id, PurchaseOrder.id == order_id
        )
        order = (await self._session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise NotFound("Purchase order not found")
        return order

    async def lines(
        self, *, organization_id: uuid.UUID, order_id: uuid.UUID
    ) -> list[PurchaseOrderLine]:
        stmt = (
            select(PurchaseOrderLine)
            .where(
                PurchaseOrderLine.organization_id == organization_id,
                PurchaseOrderLine.order_id == order_id,
            )
            .order_by(PurchaseOrderLine.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID,
        lines: list[OrderLineInput],
        ordered_on: date | None = None,
        expected_on: date | None = None,
        request_id: uuid.UUID | None = None,
        currency: str = "UZS",
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> PurchaseOrder:
        """Raise a draft order. **Posts nothing.**"""
        if not lines:
            raise ValidationFailed("A purchase order needs at least one line")
        supplier = await self._suppliers.get(
            organization_id=organization_id, supplier_id=supplier_id
        )
        if not supplier.is_active:
            raise Conflict(
                f"{supplier.name} is no longer an active supplier",
                details={"supplier_id": str(supplier.id)},
            )

        request = None
        if request_id is not None:
            request = await self._requests.get(
                organization_id=organization_id, request_id=request_id
            )
            if request.status is not RequestStatus.APPROVED:
                raise Conflict(
                    f"Request {request.code} is {request.status.value.lower()} and cannot "
                    "be ordered against",
                    details={"status": request.status.value},
                )

        order = PurchaseOrder(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="purchase_order",
                prefix="PO-",
                padding=5,
            ),
            supplier_id=supplier_id,
            request_id=request_id,
            status=OrderStatus.DRAFT,
            ordered_on=ordered_on or clinic_today(),
            expected_on=expected_on,
            raised_by_user_id=actor_user_id,
            currency=currency,
            total=ZERO,
            notes=notes,
        )
        self._session.add(order)
        await self._session.flush()

        totals: list[Decimal] = []
        for line in lines:
            if line.quantity <= ZERO:
                raise ValidationFailed("An ordered quantity must be positive")
            if line.unit_price < ZERO:
                raise ValidationFailed("A price must not be negative")
            if not line.description.strip():
                raise ValidationFailed("Each line must say what is being ordered")
            line_total = quantize(line.quantity * line.unit_price, currency)
            totals.append(line_total)
            self._session.add(
                PurchaseOrderLine(
                    organization_id=organization_id,
                    order_id=order.id,
                    item_id=line.item_id,
                    description=line.description.strip(),
                    quantity=line.quantity,
                    unit=line.unit,
                    unit_price=quantize(line.unit_price, currency),
                    line_total=line_total,
                )
            )
        order.total = sum_amounts(totals, currency)
        await self._session.flush()

        if request is not None:
            request.status = RequestStatus.ORDERED
            await self._session.flush()

        self._audit.record(
            events.PURCHASE_ORDER_CREATED,
            organization_id=organization_id,
            target_type="purchase_order",
            target_id=order.id,
            target_label=order.code,
            business_event=f"purchase_order:{order.id}",
            actor_user_id=actor_user_id,
            new_values={
                "supplier": supplier.name,
                "total": order.total,
                "lines": len(lines),
            },
        )
        return order

    async def set_status(
        self,
        *,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        status: OrderStatus,
        reason: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        duties_cannot_be_separated: bool = False,
        actor_is_owner: bool = False,
    ) -> PurchaseOrder:
        """Approve, send, cancel or close an order. **Posts nothing.**

        Approving an order one raised oneself follows the same rule as a
        refund: refused unless the organization has declared it cannot
        separate duties, or the actor is the owner — and recorded as
        self-approved when it is allowed.
        """
        order = await self.get(organization_id=organization_id, order_id=order_id)
        if order.status is status:
            return order
        allowed = ORDER_TRANSITIONS.get(order.status, frozenset())
        if status not in allowed:
            raise Conflict(
                f"A {order.status.value.lower().replace('_', ' ')} order cannot become "
                f"{status.value.lower().replace('_', ' ')}",
                details={
                    "status": order.status.value,
                    "allowed": sorted(s.value for s in allowed),
                },
            )
        if status is OrderStatus.CLOSED and not (reason or "").strip():
            raise ValidationFailed("Closing an order short needs a reason")

        self_approved = False
        if status is OrderStatus.APPROVED:
            # `raised_by_user_id` is `None` only for an order whose creator
            # was never recorded. Treating that as "not the same person" is
            # the honest direction: a control cannot be enforced on
            # information nobody has.
            if order.raised_by_user_id is not None and order.raised_by_user_id == actor_user_id:
                if not one_person_may_do_and_approve(
                    duties_cannot_be_separated=duties_cannot_be_separated,
                    actor_is_owner=actor_is_owner,
                ):
                    raise PermissionDenied(
                        "The person who raised this order must not be the one who approves it",
                        details={"control": "segregation_of_duties"},
                    )
                self_approved = True
            order.approved_by_user_id = actor_user_id
            order.approved_at = datetime.now(UTC)
            order.self_approved = self_approved

        previous = order.status
        order.status = status
        if status is OrderStatus.CLOSED:
            order.closed_reason = (reason or "").strip()
        await self._session.flush()

        self._audit.record(
            events.PURCHASE_ORDER_APPROVED
            if status is OrderStatus.APPROVED
            else events.PURCHASE_ORDER_CLOSED,
            organization_id=organization_id,
            target_type="purchase_order",
            target_id=order.id,
            target_label=order.code,
            business_event=f"purchase_order:{order.id}",
            actor_user_id=actor_user_id,
            reason=(reason or "").strip() or None,
            old_values={"status": previous.value},
            new_values={
                "status": status.value,
                "self_approved": self_approved,
                "total": order.total,
            },
        )
        return order

    async def open_orders_for(
        self, *, organization_id: uuid.UUID, supplier_id: uuid.UUID
    ) -> list[PurchaseOrder]:
        """Orders still expecting a delivery."""
        stmt = select(PurchaseOrder).where(
            PurchaseOrder.organization_id == organization_id,
            PurchaseOrder.supplier_id == supplier_id,
            PurchaseOrder.status.not_in(list(CLOSED_ORDER_STATUSES)),
        )
        return list((await self._session.execute(stmt)).scalars().all())
