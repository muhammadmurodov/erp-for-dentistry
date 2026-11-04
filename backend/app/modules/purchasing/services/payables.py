"""Supplier invoices, payments and what the clinic owes.

This is the file that posts. Three rules shape it, and all three are
borrowed from the receivables side rather than reinvented, because a payable
is a receivable seen from the other end.

**What is left on a bill is derived.** No `paid` column, no `remaining`
column. The registered total minus its live allocations, computed every time
it is asked for (ADR-026). A stored balance is a second answer that drifts
from the rows it came from, and the rows are what anybody would check it
against.

**A payment carries no invoice.** Money handed over is money handed over;
which bills it settles is a separate decision. The unallocated part is an
**advance to the supplier**, which is how this clinic actually buys — it pays
first and collects later — and a payment model demanding an invoice could not
record that at all.

**A registered bill is immutable.** Correcting one means voiding it, which
reverses its posting and keeps both entries, and entering the corrected bill
afresh. An editable posted document is not a document.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO, quantize, sum_amounts
from app.modules.accounting.enums import PostingPurpose, SourceKind
from app.modules.accounting.services.numbering import next_number
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import PostingLine, PostingRequest, PostingService
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.purchasing.enums import (
    BillKind,
    BillStatus,
    SupplierPaymentMethod,
    SupplierPaymentStatus,
)
from app.modules.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from app.modules.purchasing.services.suppliers import SupplierService

#: Which policy rule registers a bill, by what it is for.
OPERATION_BY_BILL_KIND = {
    BillKind.MATERIALS: "supplier_invoice_materials",
    BillKind.EXPENSE: "supplier_invoice_expense",
}

#: Paying down a payable, by how the money left.
OPERATION_PAYMENT_BY_METHOD = {
    SupplierPaymentMethod.CASH: "supplier_payment_cash",
    SupplierPaymentMethod.BANK: "supplier_payment_bank",
    SupplierPaymentMethod.CARD: "supplier_payment_bank",
}

#: Paying before any bill exists: an advance held as a receivable.
OPERATION_ADVANCE_BY_METHOD = {
    SupplierPaymentMethod.CASH: "supplier_advance_cash",
    SupplierPaymentMethod.BANK: "supplier_advance_bank",
    SupplierPaymentMethod.CARD: "supplier_advance_bank",
}

OPERATION_ADVANCE_APPLIED = "supplier_advance_applied"


@dataclass(frozen=True, slots=True)
class BillLineInput:
    description: str
    quantity: Decimal
    unit_price: Decimal
    unit: str | None = None
    receipt_line_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class BillBalance:
    """What is left on one bill, derived from its live allocations."""

    invoice_id: uuid.UUID
    total: Decimal
    paid: Decimal
    remaining: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class SupplierBalance:
    """What the clinic owes one supplier, and what it has paid ahead."""

    supplier_id: uuid.UUID
    billed: Decimal
    paid: Decimal
    outstanding: Decimal
    advances_available: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class PayablesAgeingRow:
    supplier_id: uuid.UUID
    supplier_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    days_over_90: Decimal
    total: Decimal


class PayablesService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._posting = PostingService(session, audit)
        self._suppliers = SupplierService(session, audit)

    # ------------------------------------------------------------- reading

    async def list_bills(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID | None = None,
        statuses: list[BillStatus] | None = None,
    ) -> list[SupplierInvoice]:
        stmt = select(SupplierInvoice).where(SupplierInvoice.organization_id == organization_id)
        if supplier_id is not None:
            stmt = stmt.where(SupplierInvoice.supplier_id == supplier_id)
        if statuses:
            stmt = stmt.where(SupplierInvoice.status.in_(statuses))
        return list(
            (await self._session.execute(stmt.order_by(SupplierInvoice.invoice_date.desc())))
            .scalars()
            .all()
        )

    async def get_bill(
        self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> SupplierInvoice:
        stmt = select(SupplierInvoice).where(
            SupplierInvoice.organization_id == organization_id,
            SupplierInvoice.id == invoice_id,
        )
        invoice = (await self._session.execute(stmt)).scalar_one_or_none()
        if invoice is None:
            raise NotFound("Supplier invoice not found")
        return invoice

    async def bill_lines(
        self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> list[SupplierInvoiceLine]:
        stmt = (
            select(SupplierInvoiceLine)
            .where(
                SupplierInvoiceLine.organization_id == organization_id,
                SupplierInvoiceLine.invoice_id == invoice_id,
            )
            .order_by(SupplierInvoiceLine.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _allocated_to(self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Decimal:
        """What is live against one bill. Reversed allocations do not count."""
        stmt = select(SupplierPaymentAllocation.amount).where(
            SupplierPaymentAllocation.organization_id == organization_id,
            SupplierPaymentAllocation.invoice_id == invoice_id,
            SupplierPaymentAllocation.reversed_at.is_(None),
        )
        return sum_amounts(list((await self._session.execute(stmt)).scalars().all()))

    async def bill_balance(
        self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> BillBalance:
        invoice = await self.get_bill(organization_id=organization_id, invoice_id=invoice_id)
        # A draft owes nothing — it is not in the books — and a voided bill
        # owes nothing either, because its posting has been reversed.
        total = invoice.total if invoice.status is BillStatus.REGISTERED else ZERO
        paid = await self._allocated_to(organization_id=organization_id, invoice_id=invoice_id)
        return BillBalance(
            invoice_id=invoice_id,
            total=total,
            paid=paid,
            remaining=quantize(total - paid, invoice.currency),
            currency=invoice.currency,
        )

    async def unallocated_of(self, *, organization_id: uuid.UUID, payment_id: uuid.UUID) -> Decimal:
        payment = await self.get_payment(organization_id=organization_id, payment_id=payment_id)
        if payment.status is SupplierPaymentStatus.REVERSED:
            return ZERO
        stmt = select(SupplierPaymentAllocation.amount).where(
            SupplierPaymentAllocation.organization_id == organization_id,
            SupplierPaymentAllocation.payment_id == payment_id,
            SupplierPaymentAllocation.reversed_at.is_(None),
        )
        allocated = sum_amounts(list((await self._session.execute(stmt)).scalars().all()))
        return quantize(payment.amount - allocated, payment.currency)

    async def supplier_balance(
        self, *, organization_id: uuid.UUID, supplier_id: uuid.UUID
    ) -> SupplierBalance:
        """What is owed to one supplier, and what has been paid ahead."""
        await self._suppliers.get(organization_id=organization_id, supplier_id=supplier_id)

        bills = await self.list_bills(
            organization_id=organization_id,
            supplier_id=supplier_id,
            statuses=[BillStatus.REGISTERED],
        )
        billed = sum_amounts([b.total for b in bills]) if bills else ZERO

        paid_rows: list[Decimal] = []
        advances: list[Decimal] = []
        payments = await self.list_payments(
            organization_id=organization_id, supplier_id=supplier_id
        )
        for payment in payments:
            if payment.status is SupplierPaymentStatus.REVERSED:
                continue
            paid_rows.append(payment.amount)
            advances.append(
                await self.unallocated_of(organization_id=organization_id, payment_id=payment.id)
            )

        allocated = ZERO
        for bill in bills:
            allocated += await self._allocated_to(
                organization_id=organization_id, invoice_id=bill.id
            )

        return SupplierBalance(
            supplier_id=supplier_id,
            billed=billed,
            paid=sum_amounts(paid_rows) if paid_rows else ZERO,
            outstanding=quantize(billed - allocated),
            advances_available=sum_amounts(advances) if advances else ZERO,
            currency="UZS",
        )

    async def ageing(
        self, *, organization_id: uuid.UUID, as_of: date | None = None
    ) -> list[PayablesAgeingRow]:
        """What is owed, by how overdue it is.

        Aged from the **due date** where there is one and the invoice date
        where there is not, because "overdue" is a question about an agreed
        term, not about when a piece of paper was written.
        """
        as_of = as_of or clinic_today()
        suppliers = {
            s.id: s
            for s in await self._suppliers.list_suppliers(
                organization_id=organization_id, include_inactive=True
            )
        }
        rows: dict[uuid.UUID, list[Decimal]] = {}
        for bill in await self.list_bills(
            organization_id=organization_id, statuses=[BillStatus.REGISTERED]
        ):
            balance = await self.bill_balance(organization_id=organization_id, invoice_id=bill.id)
            if balance.remaining <= ZERO:
                continue
            reference = bill.due_date or bill.invoice_date
            days = (as_of - reference).days
            bucket = (
                0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4
            )
            amounts = rows.setdefault(bill.supplier_id, [ZERO] * 5)
            amounts[bucket] += balance.remaining

        result: list[PayablesAgeingRow] = []
        for supplier_id, amounts in rows.items():
            supplier = suppliers.get(supplier_id)
            result.append(
                PayablesAgeingRow(
                    supplier_id=supplier_id,
                    supplier_name=supplier.name if supplier else "—",
                    current=amounts[0],
                    days_1_30=amounts[1],
                    days_31_60=amounts[2],
                    days_61_90=amounts[3],
                    days_over_90=amounts[4],
                    total=sum_amounts(amounts),
                )
            )
        result.sort(key=lambda r: r.total, reverse=True)
        return result

    # --------------------------------------------------------------- bills

    async def create_bill(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID,
        kind: BillKind,
        lines: list[BillLineInput],
        invoice_date: date | None = None,
        due_date: date | None = None,
        order_id: uuid.UUID | None = None,
        supplier_reference: str | None = None,
        currency: str = "UZS",
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> SupplierInvoice:
        """Enter a supplier's invoice as a **draft**. Posts nothing yet.

        Draft first, deliberately: entering a bill and accepting it into the
        books are two acts, and the second is where the payable appears. It
        also gives somebody a chance to compare the lines against what
        actually arrived before the liability exists.
        """
        if not lines:
            raise ValidationFailed("A supplier invoice needs at least one line")
        await self._suppliers.get(organization_id=organization_id, supplier_id=supplier_id)

        supplier_reference = (supplier_reference or "").strip() or None
        if supplier_reference is not None:
            clash = (
                await self._session.execute(
                    select(SupplierInvoice).where(
                        SupplierInvoice.organization_id == organization_id,
                        SupplierInvoice.supplier_id == supplier_id,
                        SupplierInvoice.supplier_reference == supplier_reference,
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise Conflict(
                    f"Invoice {supplier_reference} from this supplier is already "
                    f"recorded as {clash.code}",
                    details={"invoice_id": str(clash.id), "code": clash.code},
                )

        invoice = SupplierInvoice(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="supplier_invoice",
                prefix="SI-",
                padding=5,
            ),
            supplier_id=supplier_id,
            order_id=order_id,
            supplier_reference=supplier_reference,
            kind=kind,
            status=BillStatus.DRAFT,
            invoice_date=invoice_date or clinic_today(),
            due_date=due_date,
            currency=currency,
            total=ZERO,
            notes=notes,
        )
        self._session.add(invoice)
        await self._session.flush()

        totals: list[Decimal] = []
        used_receipt_lines: set[uuid.UUID] = set()
        for line in lines:
            if line.quantity <= ZERO:
                raise ValidationFailed("An invoiced quantity must be positive")
            if line.unit_price < ZERO:
                raise ValidationFailed("A price must not be negative")
            if not line.description.strip():
                raise ValidationFailed("Each line must say what is being charged for")
            if line.receipt_line_id is not None:
                if line.receipt_line_id in used_receipt_lines:
                    raise ValidationFailed(
                        "The same delivery line appears twice on this invoice",
                        details={"receipt_line_id": str(line.receipt_line_id)},
                    )
                used_receipt_lines.add(line.receipt_line_id)
                await self._check_receipt_line(
                    organization_id=organization_id,
                    supplier_id=supplier_id,
                    receipt_line_id=line.receipt_line_id,
                )
            line_total = quantize(line.quantity * line.unit_price, currency)
            totals.append(line_total)
            self._session.add(
                SupplierInvoiceLine(
                    organization_id=organization_id,
                    invoice_id=invoice.id,
                    receipt_line_id=line.receipt_line_id,
                    description=line.description.strip(),
                    quantity=line.quantity,
                    unit=line.unit,
                    unit_price=quantize(line.unit_price, currency),
                    line_total=line_total,
                )
            )
        invoice.total = sum_amounts(totals, currency)
        await self._session.flush()
        return invoice

    async def _check_receipt_line(
        self, *, organization_id: uuid.UUID, supplier_id: uuid.UUID, receipt_line_id: uuid.UUID
    ) -> None:
        """A delivery line may be billed once, by the supplier who sent it.

        This is the check the four-record shape exists for: without it, the
        same delivery can be invoiced twice and paid twice, which is the
        commonest way a small business loses money to a supplier.
        """
        stmt = (
            select(GoodsReceiptLine, GoodsReceipt.supplier_id)
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
            .where(
                GoodsReceiptLine.organization_id == organization_id,
                GoodsReceiptLine.id == receipt_line_id,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            raise NotFound("That delivery line was not found")
        if row[1] != supplier_id:
            raise ValidationFailed(
                "That delivery came from a different supplier",
                details={"receipt_line_id": str(receipt_line_id)},
            )
        already = (
            await self._session.execute(
                select(SupplierInvoiceLine).where(
                    SupplierInvoiceLine.organization_id == organization_id,
                    SupplierInvoiceLine.receipt_line_id == receipt_line_id,
                )
            )
        ).scalar_one_or_none()
        if already is not None:
            raise Conflict(
                "That delivery line has already been invoiced",
                details={"invoice_id": str(already.invoice_id)},
            )

    async def register_bill(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> SupplierInvoice:
        """Accept the bill into the books: the payable appears.

        Dr materials or expense / Cr supplier payable, under the rule for the
        bill's kind. From here the bill is immutable; correcting it means
        voiding it and entering the corrected one.
        """
        invoice = await self.get_bill(organization_id=organization_id, invoice_id=invoice_id)
        if invoice.status is not BillStatus.DRAFT:
            raise Conflict(
                f"Invoice {invoice.code} is {invoice.status.value.lower()} and cannot "
                "be registered again",
                details={"status": invoice.status.value},
            )
        if invoice.total <= ZERO:
            raise ValidationFailed("A bill with no value cannot be registered")

        policy = await self._policy_for(organization_id, invoice.invoice_date)
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=OPERATION_BY_BILL_KIND[invoice.kind],
        )

        invoice.status = BillStatus.REGISTERED
        invoice.registered_at = datetime.now(UTC)
        invoice.registered_by_user_id = actor_user_id
        await self._session.flush()

        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=invoice.invoice_date,
                description=f"Supplier invoice {invoice.code}",
                source_kind=SourceKind.SUPPLIER_INVOICE,
                source_id=str(invoice.id),
                purpose=PostingPurpose.RECOGNITION,
                currency=invoice.currency,
                operation_code=OPERATION_BY_BILL_KIND[invoice.kind],
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=invoice.total),
                    PostingLine(account_id=rule.credit_account_id, credit=invoice.total),
                ],
            ),
            actor_user_id=actor_user_id,
        )

        self._audit.record(
            events.SUPPLIER_INVOICE_REGISTERED,
            organization_id=organization_id,
            target_type="supplier_invoice",
            target_id=invoice.id,
            target_label=invoice.code,
            business_event=f"supplier_invoice:{invoice.id}",
            actor_user_id=actor_user_id,
            old_values={"status": "DRAFT"},
            new_values={
                "status": "REGISTERED",
                "total": invoice.total,
                "kind": invoice.kind.value,
            },
        )
        return invoice

    async def void_bill(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> SupplierInvoice:
        """Reverse a registered bill. Both entries stay on the record."""
        if not reason or not reason.strip():
            raise ValidationFailed("Voiding a bill requires a reason")
        invoice = await self.get_bill(organization_id=organization_id, invoice_id=invoice_id)
        if invoice.status is BillStatus.VOID:
            raise Conflict("That bill has already been voided")

        if invoice.status is BillStatus.REGISTERED:
            paid = await self._allocated_to(organization_id=organization_id, invoice_id=invoice_id)
            if paid > ZERO:
                raise Conflict(
                    "That bill has been paid. Reverse the allocation first.",
                    details={"paid": str(paid)},
                )
            entries = await self._posting.find_by_source(
                organization_id=organization_id,
                source_kind=SourceKind.SUPPLIER_INVOICE,
                source_id=str(invoice.id),
                purpose=PostingPurpose.RECOGNITION,
            )
            for entry in entries:
                await self._posting.reverse(
                    organization_id=organization_id,
                    entry_id=entry.id,
                    reason=reason.strip(),
                    actor_user_id=actor_user_id,
                )

        previous = invoice.status
        invoice.status = BillStatus.VOID
        invoice.void_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            events.SUPPLIER_INVOICE_VOIDED,
            organization_id=organization_id,
            target_type="supplier_invoice",
            target_id=invoice.id,
            target_label=invoice.code,
            business_event=f"supplier_invoice:{invoice.id}",
            actor_user_id=actor_user_id,
            reason=reason.strip(),
            old_values={"status": previous.value},
            new_values={"status": "VOID"},
        )
        return invoice

    # ------------------------------------------------------------ payments

    async def list_payments(
        self, *, organization_id: uuid.UUID, supplier_id: uuid.UUID | None = None
    ) -> list[SupplierPayment]:
        stmt = select(SupplierPayment).where(SupplierPayment.organization_id == organization_id)
        if supplier_id is not None:
            stmt = stmt.where(SupplierPayment.supplier_id == supplier_id)
        return list(
            (await self._session.execute(stmt.order_by(SupplierPayment.paid_on.desc())))
            .scalars()
            .all()
        )

    async def get_payment(
        self, *, organization_id: uuid.UUID, payment_id: uuid.UUID
    ) -> SupplierPayment:
        stmt = select(SupplierPayment).where(
            SupplierPayment.organization_id == organization_id,
            SupplierPayment.id == payment_id,
        )
        payment = (await self._session.execute(stmt)).scalar_one_or_none()
        if payment is None:
            raise NotFound("Supplier payment not found")
        return payment

    async def record_payment(
        self,
        *,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID,
        amount: Decimal,
        method: SupplierPaymentMethod,
        paid_on: date | None = None,
        invoice_id: uuid.UUID | None = None,
        reference: str | None = None,
        notes: str | None = None,
        currency: str = "UZS",
        actor_user_id: uuid.UUID | None = None,
    ) -> SupplierPayment:
        """Record money paid to a supplier, and post it.

        With `invoice_id`, the payment settles that bill: Dr payable / Cr
        cash or bank, and an allocation records which bill it went to.
        Without one, it is an **advance**: Dr advances paid / Cr cash or
        bank, held until a bill arrives to apply it to.

        Paying more than a bill's remaining balance is refused rather than
        split, because the commonest reason for it is the bill already having
        been paid — and silently turning the excess into an advance hides
        exactly that.
        """
        await self._suppliers.get(organization_id=organization_id, supplier_id=supplier_id)
        amount = quantize(amount, currency)
        if amount <= ZERO:
            raise ValidationFailed("A payment must be a positive amount")

        invoice = None
        if invoice_id is not None:
            invoice = await self.get_bill(organization_id=organization_id, invoice_id=invoice_id)
            if invoice.supplier_id != supplier_id:
                raise ValidationFailed("That bill belongs to a different supplier")
            if invoice.status is not BillStatus.REGISTERED:
                raise Conflict(
                    f"Invoice {invoice.code} is {invoice.status.value.lower()}; only a "
                    "registered bill can be paid",
                    details={"status": invoice.status.value},
                )
            balance = await self.bill_balance(
                organization_id=organization_id, invoice_id=invoice_id
            )
            if balance.remaining <= ZERO:
                raise Conflict(
                    f"Invoice {invoice.code} is already settled",
                    details={"remaining": str(balance.remaining)},
                )
            if amount > balance.remaining:
                raise ValidationFailed(
                    f"That is more than the {balance.remaining} outstanding on {invoice.code}",
                    details={"remaining": str(balance.remaining)},
                )

        paid_on = paid_on or clinic_today()
        policy = await self._policy_for(organization_id, paid_on)
        operation = (
            OPERATION_PAYMENT_BY_METHOD[method]
            if invoice is not None
            else OPERATION_ADVANCE_BY_METHOD[method]
        )
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=operation,
        )

        payment = SupplierPayment(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="supplier_payment",
                prefix="SP-",
                padding=5,
            ),
            supplier_id=supplier_id,
            amount=amount,
            currency=currency,
            method=method,
            status=SupplierPaymentStatus.RECORDED,
            paid_on=paid_on,
            reference=(reference or "").strip() or None,
            notes=notes,
            paid_by_user_id=actor_user_id,
        )
        self._session.add(payment)
        await self._session.flush()

        if invoice is not None:
            self._session.add(
                SupplierPaymentAllocation(
                    organization_id=organization_id,
                    payment_id=payment.id,
                    invoice_id=invoice.id,
                    amount=amount,
                )
            )
            await self._session.flush()

        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=paid_on,
                description=f"Supplier payment {payment.code}",
                source_kind=SourceKind.PAYMENT,
                source_id=str(payment.id),
                purpose=PostingPurpose.PAYMENT,
                currency=currency,
                operation_code=operation,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=amount),
                    PostingLine(account_id=rule.credit_account_id, credit=amount),
                ],
            ),
            actor_user_id=actor_user_id,
        )

        self._audit.record(
            events.SUPPLIER_PAYMENT_RECORDED,
            organization_id=organization_id,
            target_type="supplier_payment",
            target_id=payment.id,
            target_label=payment.code,
            business_event=f"supplier_payment:{payment.id}",
            actor_user_id=actor_user_id,
            new_values={
                "amount": amount,
                "method": method.value,
                "invoice": invoice.code if invoice else None,
                "advance": invoice is None,
            },
        )
        return payment

    async def apply_advance(
        self,
        *,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        invoice_id: uuid.UUID,
        amount: Decimal,
        actor_user_id: uuid.UUID | None = None,
    ) -> SupplierPaymentAllocation:
        """Apply money already paid to a bill that has now arrived.

        Dr payable / Cr advances paid. No cash moves and nothing is
        recognised again: the expense was recognised when the bill was
        registered, and the money left when the advance was paid.
        """
        payment = await self.get_payment(organization_id=organization_id, payment_id=payment_id)
        invoice = await self.get_bill(organization_id=organization_id, invoice_id=invoice_id)
        if payment.supplier_id != invoice.supplier_id:
            raise ValidationFailed("That payment and that bill are for different suppliers")
        if payment.status is SupplierPaymentStatus.REVERSED:
            raise Conflict("That payment has been reversed")
        if invoice.status is not BillStatus.REGISTERED:
            raise Conflict(
                f"Invoice {invoice.code} is {invoice.status.value.lower()}; only a "
                "registered bill can be settled",
                details={"status": invoice.status.value},
            )

        amount = quantize(amount, invoice.currency)
        if amount <= ZERO:
            raise ValidationFailed("An allocation must be a positive amount")

        available = await self.unallocated_of(
            organization_id=organization_id, payment_id=payment_id
        )
        if amount > available:
            raise ValidationFailed(
                f"Only {available} of that payment is unapplied",
                details={"available": str(available)},
            )
        balance = await self.bill_balance(organization_id=organization_id, invoice_id=invoice_id)
        if amount > balance.remaining:
            raise ValidationFailed(
                f"That is more than the {balance.remaining} outstanding on {invoice.code}",
                details={"remaining": str(balance.remaining)},
            )

        allocation = SupplierPaymentAllocation(
            organization_id=organization_id,
            payment_id=payment_id,
            invoice_id=invoice_id,
            amount=amount,
        )
        self._session.add(allocation)
        await self._session.flush()

        policy = await self._policy_for(organization_id, invoice.invoice_date)
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=OPERATION_ADVANCE_APPLIED,
        )
        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=invoice.invoice_date,
                description=f"Advance {payment.code} applied to {invoice.code}",
                source_kind=SourceKind.PAYMENT,
                source_id=str(allocation.id),
                purpose=PostingPurpose.SETTLEMENT,
                currency=invoice.currency,
                operation_code=OPERATION_ADVANCE_APPLIED,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=amount),
                    PostingLine(account_id=rule.credit_account_id, credit=amount),
                ],
            ),
            actor_user_id=actor_user_id,
        )

        self._audit.record(
            events.SUPPLIER_PAYMENT_ALLOCATED,
            organization_id=organization_id,
            target_type="supplier_payment",
            target_id=payment.id,
            target_label=payment.code,
            business_event=f"supplier_payment:{payment.id}",
            actor_user_id=actor_user_id,
            new_values={"invoice": invoice.code, "amount": amount},
        )
        return allocation

    async def reverse_allocation(
        self,
        *,
        organization_id: uuid.UUID,
        allocation_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> SupplierPaymentAllocation:
        """Undo an application, marking it rather than deleting it."""
        if not reason or not reason.strip():
            raise ValidationFailed("Reversing an allocation requires a reason")
        stmt = select(SupplierPaymentAllocation).where(
            SupplierPaymentAllocation.organization_id == organization_id,
            SupplierPaymentAllocation.id == allocation_id,
        )
        allocation = (await self._session.execute(stmt)).scalar_one_or_none()
        if allocation is None:
            raise NotFound("Allocation not found")
        if allocation.reversed_at is not None:
            raise Conflict("That allocation has already been reversed")

        entries = await self._posting.find_by_source(
            organization_id=organization_id,
            source_kind=SourceKind.PAYMENT,
            source_id=str(allocation.id),
            purpose=PostingPurpose.SETTLEMENT,
        )
        for entry in entries:
            await self._posting.reverse(
                organization_id=organization_id,
                entry_id=entry.id,
                reason=reason.strip(),
                actor_user_id=actor_user_id,
            )

        allocation.reversed_at = datetime.now(UTC)
        allocation.reversed_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            events.SUPPLIER_ALLOCATION_REVERSED,
            organization_id=organization_id,
            target_type="supplier_payment_allocation",
            target_id=allocation.id,
            business_event=f"supplier_payment:{allocation.payment_id}",
            actor_user_id=actor_user_id,
            reason=reason.strip(),
            new_values={"amount": allocation.amount},
        )
        return allocation

    # --------------------------------------------------------------- policy

    async def _policy_for(self, organization_id: uuid.UUID, on: date):
        return await PolicyService(self._session, self._audit).policy_in_force_on(
            organization_id=organization_id, on=on
        )
