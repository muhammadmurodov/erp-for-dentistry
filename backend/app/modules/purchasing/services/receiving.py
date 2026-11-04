"""Goods arriving, and what is still outstanding on an order.

**Receiving posts nothing**, and that is the decision this file exists to
hold. The textbook accrual at receipt debits stock and credits a
goods-received-not-invoiced account. The clinic's workbook has no such
account — its rule posts materials to 1010 against payable 6010 when the
invoice arrives — and inventing one would be inventing a statutory
classification, which this project does not do.

So a receipt records what arrived, in the quantities it arrived in, and the
bill does the posting. The gap between the two days is real and is recorded
as an open question rather than papered over with a guessed account.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.inventory.services.catalogue import CatalogueService
from app.modules.inventory.services.stock import StockService
from app.modules.purchasing.enums import RECEIVABLE_ORDER_STATUSES, OrderStatus
from app.modules.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    SupplierInvoiceLine,
)
from app.modules.purchasing.services.suppliers import PurchaseOrderService, SupplierService


@dataclass(frozen=True, slots=True)
class ReceiptLineInput:
    description: str
    quantity: Decimal
    order_line_id: uuid.UUID | None = None
    unit: str | None = None
    rejected_quantity: Decimal = ZERO
    rejected_reason: str | None = None
    #: Naming a stocked material is what puts this line on a shelf. Left out
    #: for anything the clinic does not keep stock of — a repair, a delivery
    #: charge, a one-off.
    item_id: uuid.UUID | None = None
    #: Which shelf. The organization's default is used when it is not said.
    location_id: uuid.UUID | None = None
    #: The lot number printed on the box, for a material tracked by batch.
    batch_code: str | None = None
    batch_expires_on: date | None = None


@dataclass(frozen=True, slots=True)
class OutstandingLine:
    """One order line, and how much of it is still to come."""

    order_line_id: uuid.UUID
    description: str
    unit: str | None
    ordered: Decimal
    received: Decimal
    outstanding: Decimal


class ReceivingService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._orders = PurchaseOrderService(session, audit)
        self._suppliers = SupplierService(session, audit)
        self._stock = StockService(session, audit)
        self._catalogue = CatalogueService(session, audit)

    async def list_receipts(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
    ) -> list[GoodsReceipt]:
        stmt = select(GoodsReceipt).where(GoodsReceipt.organization_id == organization_id)
        if supplier_id is not None:
            stmt = stmt.where(GoodsReceipt.supplier_id == supplier_id)
        if order_id is not None:
            stmt = stmt.where(GoodsReceipt.order_id == order_id)
        return list(
            (await self._session.execute(stmt.order_by(GoodsReceipt.received_on.desc())))
            .scalars()
            .all()
        )

    async def get(self, *, organization_id: uuid.UUID, receipt_id: uuid.UUID) -> GoodsReceipt:
        stmt = select(GoodsReceipt).where(
            GoodsReceipt.organization_id == organization_id, GoodsReceipt.id == receipt_id
        )
        receipt = (await self._session.execute(stmt)).scalar_one_or_none()
        if receipt is None:
            raise NotFound("Goods receipt not found")
        return receipt

    async def lines(
        self, *, organization_id: uuid.UUID, receipt_id: uuid.UUID
    ) -> list[GoodsReceiptLine]:
        stmt = (
            select(GoodsReceiptLine)
            .where(
                GoodsReceiptLine.organization_id == organization_id,
                GoodsReceiptLine.receipt_id == receipt_id,
            )
            .order_by(GoodsReceiptLine.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def outstanding(
        self, *, organization_id: uuid.UUID, order_id: uuid.UUID
    ) -> list[OutstandingLine]:
        """What an order is still waiting for.

        Derived from the receipt lines every time rather than kept as a
        running total on the order line. A stored "received so far" is a
        second answer that drifts the first time a receipt is corrected.
        """
        order_lines = await self._orders.lines(organization_id=organization_id, order_id=order_id)
        received_by_line: dict[uuid.UUID, Decimal] = {}
        stmt = (
            select(GoodsReceiptLine.order_line_id, GoodsReceiptLine.quantity)
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
            .where(
                GoodsReceiptLine.organization_id == organization_id,
                GoodsReceipt.order_id == order_id,
                GoodsReceiptLine.order_line_id.is_not(None),
            )
        )
        for line_id, quantity in (await self._session.execute(stmt)).all():
            received_by_line[line_id] = received_by_line.get(line_id, ZERO) + quantity

        rows: list[OutstandingLine] = []
        for line in order_lines:
            received = received_by_line.get(line.id, ZERO)
            rows.append(
                OutstandingLine(
                    order_line_id=line.id,
                    description=line.description,
                    unit=line.unit,
                    ordered=line.quantity,
                    received=received,
                    # Never negative: over-delivery is recorded as received,
                    # and "minus three outstanding" would read as a debt.
                    outstanding=max(line.quantity - received, ZERO),
                )
            )
        return rows

    async def receive(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID,
        lines: list[ReceiptLineInput],
        order_id: uuid.UUID | None = None,
        received_on: date | None = None,
        delivery_note: str | None = None,
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> GoodsReceipt:
        """Record what arrived. **Posts nothing.**

        A receipt without an order is allowed: the clinic buys gypsum from a
        market stall and is handed a box, and refusing to record that would
        not stop it happening — it would move it off the books.
        """
        if not lines:
            raise ValidationFailed("A goods receipt needs at least one line")
        await self._suppliers.get(organization_id=organization_id, supplier_id=supplier_id)

        order = None
        if order_id is not None:
            order = await self._orders.get(organization_id=organization_id, order_id=order_id)
            if order.supplier_id != supplier_id:
                raise ValidationFailed(
                    "That order belongs to a different supplier",
                    details={"order_id": str(order_id)},
                )
            if order.status not in RECEIVABLE_ORDER_STATUSES:
                raise Conflict(
                    f"Order {order.code} is {order.status.value.lower().replace('_', ' ')} "
                    "and is not expecting a delivery",
                    details={"status": order.status.value},
                )

        receipt = GoodsReceipt(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="goods_receipt",
                prefix="GR-",
                padding=5,
            ),
            supplier_id=supplier_id,
            order_id=order_id,
            received_on=received_on or clinic_today(),
            received_by_user_id=actor_user_id,
            delivery_note=delivery_note,
            notes=notes,
        )
        self._session.add(receipt)
        await self._session.flush()

        order_line_ids = (
            {
                line.id
                for line in await self._orders.lines(
                    organization_id=organization_id, order_id=order_id
                )
            }
            if order_id is not None
            else set()
        )

        for line in lines:
            if line.quantity <= ZERO:
                raise ValidationFailed("A received quantity must be positive")
            if not line.description.strip():
                raise ValidationFailed("Each line must say what arrived")
            if line.rejected_quantity < ZERO:
                raise ValidationFailed("A rejected quantity must not be negative")
            if line.rejected_quantity > ZERO and not (line.rejected_reason or "").strip():
                raise ValidationFailed("Goods turned away must say why")
            if line.order_line_id is not None and line.order_line_id not in order_line_ids:
                raise ValidationFailed(
                    "That line is not on this order",
                    details={"order_line_id": str(line.order_line_id)},
                )
            receipt_line = GoodsReceiptLine(
                organization_id=organization_id,
                receipt_id=receipt.id,
                order_line_id=line.order_line_id,
                item_id=line.item_id,
                description=line.description.strip(),
                quantity=line.quantity,
                unit=line.unit,
                rejected_quantity=line.rejected_quantity,
                rejected_reason=(line.rejected_reason or "").strip() or None,
            )
            self._session.add(receipt_line)
            await self._session.flush()
            await self._shelve(
                organization_id=organization_id,
                line=line,
                receipt_line=receipt_line,
                received_on=receipt.received_on,
                actor_user_id=actor_user_id,
            )
        await self._session.flush()

        if order is not None:
            outstanding = await self.outstanding(organization_id=organization_id, order_id=order.id)
            everything_arrived = all(row.outstanding <= ZERO for row in outstanding)
            order.status = (
                OrderStatus.RECEIVED if everything_arrived else OrderStatus.PARTIALLY_RECEIVED
            )
            await self._session.flush()

        self._audit.record(
            events.GOODS_RECEIVED,
            organization_id=organization_id,
            target_type="goods_receipt",
            target_id=receipt.id,
            target_label=receipt.code,
            business_event=f"goods_receipt:{receipt.id}",
            actor_user_id=actor_user_id,
            new_values={
                "lines": len(lines),
                "order": order.code if order else None,
                "order_status": order.status.value if order else None,
            },
        )
        return receipt

    async def _shelve(
        self,
        *,
        organization_id: uuid.UUID,
        line: ReceiptLineInput,
        receipt_line: GoodsReceiptLine,
        received_on: date,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        """Put an accepted quantity on a shelf, in the same transaction.

        **Still posts nothing.** This moves a quantity, not an amount: what
        the delivery cost reaches the books when the supplier invoice is
        registered, exactly as it did before inventory existed.

        Only what was *accepted* is shelved. Goods turned away at the door
        never entered the clinic, and recording them as stock and then
        writing them off again would invent two movements for something that
        never happened.
        """
        if line.item_id is None:
            return
        accepted = line.quantity - line.rejected_quantity
        if accepted <= ZERO:
            return

        item = await self._catalogue.get_item(
            organization_id=organization_id, item_id=line.item_id
        )
        location_id = line.location_id
        if location_id is None:
            location_id = (
                await self._catalogue.default_location(organization_id=organization_id)
            ).id

        batch_id = None
        if item.is_batch_tracked:
            if not (line.batch_code or "").strip():
                raise ValidationFailed(
                    f"{item.name} is tracked by batch, so this delivery has to "
                    "say which lot arrived",
                    details={"item_id": str(item.id)},
                )
            batch = await self._catalogue.ensure_batch(
                organization_id=organization_id,
                item_id=item.id,
                code=line.batch_code or "",
                expires_on=line.batch_expires_on,
            )
            batch_id = batch.id

        await self._stock.receive(
            organization_id=organization_id,
            item_id=item.id,
            location_id=location_id,
            quantity=accepted,
            receipt_line_id=receipt_line.id,
            batch_id=batch_id,
            occurred_on=received_on,
            actor_user_id=actor_user_id,
        )

    async def uninvoiced_receipt_lines(
        self, *, organization_id: uuid.UUID, supplier_id: uuid.UUID
    ) -> list[GoodsReceiptLine]:
        """What has arrived from this supplier and not yet been billed.

        The question the whole four-record shape exists to answer, and the
        one that catches a supplier invoicing the same delivery twice.
        """
        billed = select(SupplierInvoiceLine.receipt_line_id).where(
            SupplierInvoiceLine.organization_id == organization_id,
            SupplierInvoiceLine.receipt_line_id.is_not(None),
        )
        stmt = (
            select(GoodsReceiptLine)
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
            .where(
                GoodsReceiptLine.organization_id == organization_id,
                GoodsReceipt.supplier_id == supplier_id,
                GoodsReceiptLine.id.not_in(billed),
            )
            .order_by(GoodsReceipt.received_on)
        )
        return list((await self._session.execute(stmt)).scalars().all())
