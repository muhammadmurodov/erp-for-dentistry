"""What the stock on the shelf is worth — if anybody has said how to decide.

This file lives in purchasing rather than in inventory, and that placement
is the decision it exists to hold. Valuing stock needs two things that
belong to two different modules: **how much** is on the shelf, which is
inventory's quantity ledger, and **what it cost**, which is the supplier
invoices purchasing already holds. Putting it in inventory would mean
inventory importing purchasing while purchasing imports inventory to put
deliveries on the shelf, and a cycle is how a modular monolith stops being
modular. Purchasing already composes inventory in one direction, so the
composition stays there — the same shape as the cash module composing
purchasing to pay a supplier out of the till.

**Nothing here posts.** It answers a question; it does not move money. While
the organization's valuation method is `UNDECLARED` it answers honestly: the
quantities are real and every cost is null, because `OPEN_QUESTIONS.md` I1
has not been answered and a guessed cost would end up in a balance sheet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.core.money import ZERO, quantize
from app.modules.audit.service import AuditRecorder
from app.modules.inventory.enums import InventoryValuationMethod
from app.modules.inventory.services.stock import StockService
from app.modules.organization.service import OrganizationService
from app.modules.purchasing.enums import BillStatus
from app.modules.purchasing.models import (
    GoodsReceiptLine,
    SupplierInvoice,
    SupplierInvoiceLine,
)


@dataclass(frozen=True, slots=True)
class ValuedItem:
    item_id: uuid.UUID
    code: str
    name: str
    unit: str
    quantity: Decimal
    unit_cost: Decimal | None
    value: Decimal | None


@dataclass(frozen=True, slots=True)
class Valuation:
    method: InventoryValuationMethod
    currency: str
    total_value: Decimal | None
    unpriced_items: int
    rows: list[ValuedItem]


class StockValuationService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._stock = StockService(session, audit)
        self._organizations = OrganizationService(session, audit)

    async def value(self, *, organization_id: uuid.UUID) -> Valuation:
        """Quantities always; costs only once a method has been declared."""
        organization = await self._organizations.get(organization_id)
        method = InventoryValuationMethod(organization.inventory_valuation_method)
        balances = await self._stock.balances(organization_id=organization_id)

        totals: dict[uuid.UUID, ValuedItem] = {}
        for row in balances:
            existing = totals.get(row.item_id)
            quantity = (existing.quantity if existing else ZERO) + row.quantity
            totals[row.item_id] = ValuedItem(
                item_id=row.item_id,
                code=row.item_code,
                name=row.item_name,
                unit=row.unit,
                quantity=quantity,
                unit_cost=None,
                value=None,
            )

        if method is InventoryValuationMethod.UNDECLARED:
            return Valuation(
                method=method,
                currency=organization.base_currency,
                total_value=None,
                unpriced_items=len(totals),
                rows=sorted(totals.values(), key=lambda r: r.name),
            )

        if method is InventoryValuationMethod.FIFO:
            raise ValidationFailed(
                "First-in, first-out valuation needs cost layers this system "
                "does not keep. Weighted average is derivable from the "
                "supplier invoices already on file; FIFO is not, and is not "
                "guessed.",
                details={"setting": "inventory_valuation_method"},
            )

        costs = await self._weighted_average_costs(organization_id=organization_id)
        rows: list[ValuedItem] = []
        total = ZERO
        unpriced = 0
        for row in totals.values():
            unit_cost = costs.get(row.item_id)
            if unit_cost is None:
                unpriced += 1
                rows.append(row)
                continue
            value = quantize(unit_cost * row.quantity, organization.base_currency)
            total += value
            rows.append(
                ValuedItem(
                    item_id=row.item_id,
                    code=row.code,
                    name=row.name,
                    unit=row.unit,
                    quantity=row.quantity,
                    unit_cost=unit_cost,
                    value=value,
                )
            )
        return Valuation(
            method=method,
            currency=organization.base_currency,
            total_value=total,
            unpriced_items=unpriced,
            rows=sorted(rows, key=lambda r: r.name),
        )

    async def _weighted_average_costs(
        self, *, organization_id: uuid.UUID
    ) -> dict[uuid.UUID, Decimal]:
        """The average the clinic actually paid, from its registered bills.

        Only registered bills count: a draft is what somebody typed, and a
        void one is a bill the clinic says never happened. The quantity used
        as the weight is the quantity *billed*, not the quantity received,
        because the price being averaged is the price on that line.
        """
        stmt = (
            select(
                GoodsReceiptLine.item_id,
                SupplierInvoiceLine.quantity,
                SupplierInvoiceLine.line_total,
            )
            .join(
                SupplierInvoiceLine,
                SupplierInvoiceLine.receipt_line_id == GoodsReceiptLine.id,
            )
            .join(SupplierInvoice, SupplierInvoice.id == SupplierInvoiceLine.invoice_id)
            .where(
                GoodsReceiptLine.organization_id == organization_id,
                GoodsReceiptLine.item_id.is_not(None),
                SupplierInvoice.status == BillStatus.REGISTERED,
            )
        )
        quantities: dict[uuid.UUID, Decimal] = {}
        amounts: dict[uuid.UUID, Decimal] = {}
        for item_id, quantity, line_total in (await self._session.execute(stmt)).all():
            if quantity is None or quantity <= ZERO:
                continue
            quantities[item_id] = quantities.get(item_id, ZERO) + quantity
            amounts[item_id] = amounts.get(item_id, ZERO) + line_total
        return {
            item_id: amounts[item_id] / quantity
            for item_id, quantity in quantities.items()
            if quantity > ZERO
        }
