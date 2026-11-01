"""Invoicing, payments and allocation.

The rules this file exists to enforce, in the order they matter:

1. **Four distinct events.** Service completion, invoice issuance, revenue
   recognition and payment collection are not interchangeable. Issuing is
   what recognises revenue and creates a receivable.
2. **No over-allocation.** The sum of a payment's allocations may never
   exceed the payment, and the sum of an invoice's allocations may never
   exceed the invoice. Both are checked under a row lock, because a
   check-then-act gap is exactly where a double application appears.
3. **An advance is a liability.** An unallocated payment is money owed back
   as service, not revenue earned.
4. **Nothing posted is edited.** An issued invoice is voided by reversal; a
   payment is refunded by reversal. The originals stay visible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO, quantize, to_amount
from app.modules.accounting.enums import PostingPurpose, SourceKind
from app.modules.accounting.services.numbering import next_number
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import (
    PostingLine,
    PostingRequest,
    PostingService,
)
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.sales.enums import (
    OPEN_INVOICE_STATUSES,
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    RevenueTaxTreatment,
)
from app.modules.sales.models import (
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
    Service,
)

#: Operation codes this module posts under. Each must exist in the
#: organization's active accounting policy; the debit and credit accounts are
#: the policy's business, not this module's.
OPERATION_INVOICE_ISSUED = "invoice_issued"
OPERATION_INVOICE_VOIDED = "invoice_voided"
OPERATION_PAYMENT_BY_METHOD = {
    PaymentMethod.CASH: "payment_received_cash",
    PaymentMethod.CARD: "payment_received_card",
    PaymentMethod.BANK_TRANSFER: "payment_received_bank",
}
#: Posted when money arrives with nothing yet to apply it to.
OPERATION_ADVANCE_BY_METHOD = {
    PaymentMethod.CASH: "advance_received_cash",
    PaymentMethod.CARD: "advance_received_card",
    PaymentMethod.BANK_TRANSFER: "advance_received_bank",
}
#: Posted when an advance is later applied to an invoice.
OPERATION_ADVANCE_APPLIED = "advance_applied_to_invoice"
#: Clearing a debt the clinic has given up collecting. Deliberately absent
#: from the default policy: which account absorbs a bad patient debt is
#: `OPEN_QUESTIONS.md` A7, and until an accountant answers it the write-off
#: fails loudly rather than posting to a guessed account (ADR-039).
OPERATION_DEBT_WRITTEN_OFF = "debt_written_off"


@dataclass(frozen=True, slots=True)
class InvoiceLineInput:
    description: str
    quantity: Decimal = Decimal("1")
    service_id: uuid.UUID | None = None
    unit_price: Decimal | None = None
    """Taken from the price list when omitted."""


@dataclass(frozen=True, slots=True)
class CustomerBalance:
    """What a patient owes and what they have on account.

    `invoiced` counts everything genuinely billed, written-off debts
    included — the revenue was earned. `outstanding` is what is still
    collectable, so a written-off debt drops out of it. Keeping both means
    the screen can show "billed 2 000 000, of which 300 000 written off"
    rather than quietly shrinking the patient's history.
    """

    customer_id: uuid.UUID
    invoiced: Decimal
    paid: Decimal
    outstanding: Decimal
    unapplied_credit: Decimal
    written_off: Decimal = Decimal("0")


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditRecorder,
        posting: PostingService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._posting = posting or PostingService(session, audit)

    # ----------------------------------------------------------- invoicing

    async def create_invoice(
        self,
        *,
        organization_id: uuid.UUID,
        customer_id: uuid.UUID,
        issue_date: date,
        lines: list[InvoiceLineInput],
        due_date: date | None = None,
        currency: str = "UZS",
        notes: str | None = None,
    ) -> Invoice:
        """Prepare a draft. A draft has no ledger effect and no receivable."""
        if not lines:
            raise ValidationFailed("An invoice needs at least one line")

        from app.modules.sales.services.catalogue import CatalogueService

        catalogue = CatalogueService(self._session, self._audit)
        await catalogue.get_customer(organization_id=organization_id, customer_id=customer_id)

        invoice = Invoice(
            organization_id=organization_id,
            number=await next_number(
                self._session,
                organization_id=organization_id,
                name="invoice",
                prefix="INV-",
            ),
            customer_id=customer_id,
            status=InvoiceStatus.DRAFT,
            issue_date=issue_date,
            due_date=due_date,
            currency=currency.upper(),
            notes=notes,
        )
        self._session.add(invoice)
        await self._session.flush()

        subtotal = ZERO
        for number, line in enumerate(lines, start=1):
            quantity = to_amount(line.quantity)
            if quantity <= ZERO:
                raise ValidationFailed(f"Line {number}: quantity must be positive")

            service: Service | None = None
            unit_price = line.unit_price
            mxik = None
            if line.service_id is not None:
                service = await catalogue.get_service(
                    organization_id=organization_id, service_id=line.service_id
                )
                mxik = service.mxik_code
                if unit_price is None:
                    price = await catalogue.price_on(
                        organization_id=organization_id,
                        service_id=line.service_id,
                        on=issue_date,
                    )
                    if price is None:
                        raise ValidationFailed(
                            f"Line {number}: service {service.code} has no price on "
                            f"{issue_date.isoformat()}. Set a price or give one here.",
                            details={"service_code": service.code},
                        )
                    unit_price = price.amount
            if unit_price is None:
                raise ValidationFailed(f"Line {number}: a unit price is required")

            unit_price = quantize(unit_price, currency)
            line_total = quantize(quantity * unit_price, currency)
            subtotal += line_total

            self._session.add(
                InvoiceLine(
                    organization_id=organization_id,
                    invoice_id=invoice.id,
                    line_number=number,
                    service_id=line.service_id,
                    description=line.description,
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                    mxik_code=mxik,
                )
            )

        invoice.subtotal = quantize(subtotal, currency)
        invoice.tax_total = ZERO
        invoice.total = invoice.subtotal
        await self._session.flush()

        self._audit.record(
            "sales.invoice.created",
            organization_id=organization_id,
            target_type="invoice",
            target_id=invoice.id,
            target_label=invoice.number,
            new_values={"status": "DRAFT", "total": invoice.total, "lines": len(lines)},
        )
        return invoice

    async def issue_invoice(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        tax_treatment: RevenueTaxTreatment,
        actor_user_id: uuid.UUID | None = None,
    ) -> Invoice:
        """Issue a draft: recognise revenue and create the receivable.

        `tax_treatment` must be stated by the caller rather than defaulted.
        The clinic's fiscal receipts report zero VAT while its bank statement
        shows VAT being paid, and until an accountant resolves that
        contradiction the system will not choose on their behalf — see
        `docs/OPEN_QUESTIONS.md` question A5.
        """
        invoice = await self.get_invoice(organization_id=organization_id, invoice_id=invoice_id)
        if invoice.status is not InvoiceStatus.DRAFT:
            raise Conflict(
                f"Invoice {invoice.number} is {invoice.status.value.lower()} "
                "and cannot be issued again",
                details={"status": invoice.status.value},
            )
        if tax_treatment is RevenueTaxTreatment.UNDECLARED:
            raise ValidationFailed(
                "This organization has not declared how revenue is taxed, so an "
                "invoice cannot be issued. An accountant must confirm the VAT "
                "treatment first (see OPEN_QUESTIONS A5).",
                details={"setting": "revenue_tax_treatment"},
            )
        if tax_treatment is RevenueTaxTreatment.VAT_INCLUSIVE:
            raise ValidationFailed(
                "VAT-inclusive pricing is not implemented: the rate and whether "
                "dental services bear VAT at all are unconfirmed "
                "(OPEN_QUESTIONS A5). Nothing is guessed here.",
                details={"setting": "revenue_tax_treatment"},
            )

        policy = await self._policy_for(organization_id, invoice.issue_date)
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=OPERATION_INVOICE_ISSUED,
        )

        invoice.status = InvoiceStatus.ISSUED
        invoice.tax_treatment = tax_treatment.value
        invoice.issued_at = datetime.now(UTC)
        invoice.issued_by_user_id = actor_user_id
        await self._session.flush()

        # Dr receivable / Cr revenue. Revenue is recognised here — once.
        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=invoice.issue_date,
                description=f"Invoice {invoice.number}",
                source_kind=SourceKind.INVOICE,
                source_id=str(invoice.id),
                purpose=PostingPurpose.RECOGNITION,
                currency=invoice.currency,
                operation_code=OPERATION_INVOICE_ISSUED,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=invoice.total),
                    PostingLine(account_id=rule.credit_account_id, credit=invoice.total),
                ],
            ),
            actor_user_id=actor_user_id,
        )

        self._audit.record(
            "sales.invoice.issued",
            organization_id=organization_id,
            target_type="invoice",
            target_id=invoice.id,
            target_label=invoice.number,
            business_event=f"invoice:{invoice.id}",
            old_values={"status": "DRAFT"},
            new_values={
                "status": "ISSUED",
                "total": invoice.total,
                "tax_treatment": tax_treatment.value,
            },
        )
        return invoice

    async def void_invoice(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> Invoice:
        """Reverse an issued invoice. The original stays visible."""
        if not reason or not reason.strip():
            raise ValidationFailed("Voiding an invoice requires a reason")

        invoice = await self.get_invoice(organization_id=organization_id, invoice_id=invoice_id)
        if invoice.status is InvoiceStatus.DRAFT:
            raise Conflict(
                "A draft is cancelled, not voided — it was never posted",
                details={"status": invoice.status.value},
            )
        if invoice.status is InvoiceStatus.VOID:
            raise Conflict(f"Invoice {invoice.number} is already void")

        allocated = await self.allocated_to_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        if allocated > ZERO:
            # Unapplying payments is a separate, deliberate act; doing it
            # implicitly here would silently move somebody's money.
            raise Conflict(
                f"Invoice {invoice.number} has {allocated} allocated to it. "
                "Remove the allocations before voiding it.",
                details={"allocated": str(allocated)},
            )

        entries = await self._posting.find_by_source(
            organization_id=organization_id,
            source_kind=SourceKind.INVOICE,
            source_id=str(invoice.id),
            purpose=PostingPurpose.RECOGNITION,
        )
        for entry in entries:
            if entry.reversed_by_entry_id is None:
                await self._posting.reverse(
                    organization_id=organization_id,
                    entry_id=entry.id,
                    reason=f"Invoice {invoice.number} voided: {reason.strip()}",
                    actor_user_id=actor_user_id,
                    reversal_date=date.today(),
                )

        invoice.status = InvoiceStatus.VOID
        invoice.voided_at = datetime.now(UTC)
        invoice.void_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            "sales.invoice.voided",
            organization_id=organization_id,
            target_type="invoice",
            target_id=invoice.id,
            target_label=invoice.number,
            reason=reason.strip(),
            old_values={"status": "ISSUED"},
            new_values={"status": "VOID"},
        )
        return invoice

    async def write_off_debt(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        reason: str,
        written_off_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Invoice:
        """Give up on collecting what is still outstanding on an invoice.

        A write-off is **not** a void. Voiding says the invoice should never
        have existed and reverses the revenue, restating a period that may
        already have been reported. A write-off says the revenue was earned
        and the money will not arrive: the receivable goes, the revenue
        stays, and the loss lands in the period the decision was taken.

        Only the outstanding part is written off, so a part-paid invoice
        keeps the payments that were made.
        """
        if not reason or not reason.strip():
            raise ValidationFailed("A write-off requires a reason")

        invoice = await self._locked_invoice(organization_id, invoice_id)
        if invoice.status not in OPEN_INVOICE_STATUSES:
            raise Conflict(
                f"Invoice {invoice.number} is {invoice.status.value.lower()} "
                "and has no debt to write off",
                details={"status": invoice.status.value},
            )
        if invoice.written_off_at is not None:
            raise Conflict(f"Invoice {invoice.number} has already been written off")

        outstanding = await self.outstanding(organization_id=organization_id, invoice_id=invoice_id)
        if outstanding <= ZERO:
            raise Conflict(f"Invoice {invoice.number} has nothing outstanding to write off")

        on = written_off_on or date.today()
        policy = await self._policy_for(organization_id, on)
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=OPERATION_DEBT_WRITTEN_OFF,
        )
        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=on,
                description=f"Debt written off: invoice {invoice.number}",
                source_kind=SourceKind.INVOICE,
                source_id=str(invoice.id),
                purpose=PostingPurpose.ADJUSTMENT,
                currency=invoice.currency,
                operation_code=OPERATION_DEBT_WRITTEN_OFF,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=outstanding),
                    PostingLine(account_id=rule.credit_account_id, credit=outstanding),
                ],
            ),
            actor_user_id=actor_user_id,
        )

        invoice.written_off_at = datetime.now(UTC)
        invoice.write_off_reason = reason.strip()
        invoice.written_off_by_user_id = actor_user_id
        invoice.status = InvoiceStatus.WRITTEN_OFF
        await self._session.flush()

        self._audit.record(
            events.DEBT_WRITTEN_OFF,
            organization_id=organization_id,
            target_type="invoice",
            target_id=invoice.id,
            target_label=invoice.number,
            reason=reason.strip(),
            business_event=f"invoice:{invoice.id}",
            old_values={"outstanding": outstanding},
            new_values={"written_off": True, "amount": outstanding},
        )
        return invoice

    # ------------------------------------------------------------ payments

    async def record_payment(
        self,
        *,
        organization_id: uuid.UUID,
        customer_id: uuid.UUID,
        amount: Decimal,
        method: PaymentMethod,
        received_on: date,
        currency: str = "UZS",
        reference: str | None = None,
        notes: str | None = None,
        allocate_to: list[tuple[uuid.UUID, Decimal]] | None = None,
        auto_allocate: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> Payment:
        """Record money received and post it.

        Whatever is left unallocated is an **advance** — a liability, because
        the organization owes a service for it. It becomes revenue only when
        it is applied to an invoice.
        """
        amount = quantize(amount, currency)
        if amount <= ZERO:
            raise ValidationFailed("A payment must be a positive amount")

        from app.modules.sales.services.catalogue import CatalogueService

        await CatalogueService(self._session, self._audit).get_customer(
            organization_id=organization_id, customer_id=customer_id
        )

        payment = Payment(
            organization_id=organization_id,
            number=await next_number(
                self._session,
                organization_id=organization_id,
                name="payment",
                prefix="PMT-",
            ),
            customer_id=customer_id,
            received_on=received_on,
            method=method,
            status=PaymentStatus.RECORDED,
            amount=amount,
            currency=currency.upper(),
            reference=reference,
            notes=notes,
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(payment)
        await self._session.flush()

        targets = list(allocate_to or [])
        if auto_allocate and not targets:
            targets = await self._oldest_first_allocation(
                organization_id=organization_id,
                customer_id=customer_id,
                available=amount,
            )
        for invoice_id, allocation_amount in targets:
            await self.allocate(
                organization_id=organization_id,
                payment_id=payment.id,
                invoice_id=invoice_id,
                amount=allocation_amount,
                actor_user_id=actor_user_id,
                post=False,
            )

        allocated = await self.allocated_from_payment(
            organization_id=organization_id, payment_id=payment.id
        )
        await self._post_payment(
            organization_id=organization_id,
            payment=payment,
            applied=allocated,
            actor_user_id=actor_user_id,
        )

        self._audit.record(
            events.PAYMENT_RECORDED,
            organization_id=organization_id,
            target_type="payment",
            target_id=payment.id,
            target_label=payment.number,
            business_event=f"payment:{payment.id}",
            new_values={
                "amount": amount,
                "method": method.value,
                "allocated": allocated,
                "advance": quantize(amount - allocated, currency),
            },
        )
        await self._refresh_invoice_statuses(
            organization_id, [invoice_id for invoice_id, _ in targets]
        )
        return payment

    async def _post_payment(
        self,
        *,
        organization_id: uuid.UUID,
        payment: Payment,
        applied: Decimal,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        """Post the cash movement, splitting applied from advance.

        A payment that settles an invoice reduces a receivable. A payment
        with nothing to settle creates a liability. A payment that does both
        produces one entry with three lines rather than two entries, because
        it is one movement of money.
        """
        policy = await self._policy_for(organization_id, payment.received_on)
        advance = quantize(payment.amount - applied, payment.currency)

        lines: list[PostingLine] = []
        settle_rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=OPERATION_PAYMENT_BY_METHOD[payment.method],
        )
        # Debit side: the cash, bank or card-transit account money arrived in.
        lines.append(PostingLine(account_id=settle_rule.debit_account_id, debit=payment.amount))

        if applied > ZERO:
            lines.append(PostingLine(account_id=settle_rule.credit_account_id, credit=applied))
        if advance > ZERO:
            advance_rule = await self._posting.resolve_policy_rule(
                organization_id=organization_id,
                policy_id=policy.id,
                operation_code=OPERATION_ADVANCE_BY_METHOD[payment.method],
            )
            lines.append(PostingLine(account_id=advance_rule.credit_account_id, credit=advance))

        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=payment.received_on,
                description=f"Payment {payment.number} ({payment.method.value.lower()})",
                source_kind=SourceKind.PAYMENT,
                source_id=str(payment.id),
                purpose=PostingPurpose.PAYMENT,
                currency=payment.currency,
                operation_code=OPERATION_PAYMENT_BY_METHOD[payment.method],
                policy_id=policy.id,
                lines=lines,
                audit_context={"applied": applied, "advance": advance},
            ),
            actor_user_id=actor_user_id,
        )

    async def refund_payment(
        self,
        *,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
        self_approved: bool = False,
    ) -> Payment:
        """Reverse a payment in full. Requires approval authority.

        `self_approved` is recorded, not enforced: whether one person may both
        take a payment and give it back is decided at the edge, where the
        organization's duty-separation declaration is known (ADR-041). This
        only makes sure the resulting audit row says which it was.
        """
        if not reason or not reason.strip():
            raise ValidationFailed("A refund requires a reason")

        payment = await self.get_payment(organization_id=organization_id, payment_id=payment_id)
        if payment.status is PaymentStatus.REFUNDED:
            raise Conflict(f"Payment {payment.number} has already been refunded")

        # Unapply first, and reverse each application's own entry. Reversing
        # only the payment entry left the advance-application entries
        # standing: account 6310 ended up with a debit balance — a negative
        # liability — while the invoice reopened unpaid (ADR-038).
        allocations = await self.list_allocations(
            organization_id=organization_id, payment_id=payment_id
        )
        touched = [a.invoice_id for a in allocations]
        for allocation in allocations:
            await self._reverse_allocation_posting(
                organization_id=organization_id,
                allocation_id=allocation.id,
                reason=f"Payment {payment.number} refunded: {reason.strip()}",
                actor_user_id=actor_user_id,
            )
            allocation.reversed_at = datetime.now(UTC)
            allocation.reversal_reason = f"Payment {payment.number} refunded"
            allocation.reversed_by_user_id = actor_user_id
        await self._session.flush()

        entries = await self._posting.find_by_source(
            organization_id=organization_id,
            source_kind=SourceKind.PAYMENT,
            source_id=str(payment.id),
            purpose=PostingPurpose.PAYMENT,
        )
        for entry in entries:
            if entry.reversed_by_entry_id is None:
                await self._posting.reverse(
                    organization_id=organization_id,
                    entry_id=entry.id,
                    reason=f"Payment {payment.number} refunded: {reason.strip()}",
                    actor_user_id=actor_user_id,
                    reversal_date=date.today(),
                )

        payment.status = PaymentStatus.REFUNDED
        payment.refunded_at = datetime.now(UTC)
        payment.refund_reason = reason.strip()
        await self._session.flush()
        await self._refresh_invoice_statuses(organization_id, touched)

        self._audit.record(
            events.REFUND_APPROVED,
            organization_id=organization_id,
            target_type="payment",
            target_id=payment.id,
            target_label=payment.number,
            reason=reason.strip(),
            business_event=f"payment:{payment.id}",
            old_values={"status": "RECORDED"},
            new_values={
                "status": "REFUNDED",
                "amount": payment.amount,
                # True when the person who took the money also gave it back,
                # which only an organization that has declared it cannot
                # separate duties may do (ADR-041).
                "self_approved": self_approved,
            },
        )
        return payment

    # ---------------------------------------------------------- allocation

    async def allocate(
        self,
        *,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        invoice_id: uuid.UUID,
        amount: Decimal,
        actor_user_id: uuid.UUID | None = None,
        post: bool = True,
    ) -> PaymentAllocation:
        """Apply part of a payment to an invoice.

        Both sides are locked before the sums are checked. Without the lock,
        two concurrent allocations could each read the same remaining balance
        and both succeed — the scenario the specification calls out, where
        two cashiers apply the same payment at once.

        **The invoice is locked first, then the payment**, and every other
        path that touches an allocation takes them in that order too: the
        composite settlement above this module, and the database trigger that
        backs the rule up. One order, consistently, is what makes a deadlock
        between two settlements impossible rather than merely unlikely.
        """
        invoice = await self._locked_invoice(organization_id, invoice_id)
        payment = await self._locked_payment(organization_id, payment_id)

        if payment.status is PaymentStatus.REFUNDED:
            raise Conflict(f"Payment {payment.number} has been refunded")
        if invoice.status not in OPEN_INVOICE_STATUSES:
            raise Conflict(
                f"Invoice {invoice.number} is {invoice.status.value.lower()} "
                "and cannot receive a payment",
                details={"status": invoice.status.value},
            )
        if payment.currency != invoice.currency:
            raise ValidationFailed(
                "A payment can only be applied to an invoice in the same currency",
                details={
                    "payment_currency": payment.currency,
                    "invoice_currency": invoice.currency,
                },
            )

        amount = quantize(amount, payment.currency)
        if amount <= ZERO:
            raise ValidationFailed("An allocation must be a positive amount")

        already_from_payment = await self.allocated_from_payment(
            organization_id=organization_id, payment_id=payment_id
        )
        available = quantize(payment.amount - already_from_payment, payment.currency)
        if amount > available:
            raise ValidationFailed(
                f"Payment {payment.number} has only {available} left to allocate",
                details={"available": str(available), "requested": str(amount)},
            )

        already_on_invoice = await self.allocated_to_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        outstanding = quantize(invoice.total - already_on_invoice, invoice.currency)
        if amount > outstanding:
            raise ValidationFailed(
                f"Invoice {invoice.number} only has {outstanding} outstanding",
                details={"outstanding": str(outstanding), "requested": str(amount)},
            )

        allocation = PaymentAllocation(
            organization_id=organization_id,
            payment_id=payment_id,
            invoice_id=invoice_id,
            amount=amount,
            allocated_by_user_id=actor_user_id,
        )
        self._session.add(allocation)
        # The checks above are the readable guard; these are the ones that
        # hold when two transactions interleave. Both are enforced by the
        # database — a unique index over live rows, and a trigger that
        # re-sums under the same row locks this method already holds — so a
        # duplicated request cannot settle an invoice twice even if it
        # reaches the tables by some other route (ADR-039).
        try:
            await self._session.flush()
        except IntegrityError as exc:
            detail = str(exc.orig)
            if "uq_payment_allocation_live" in detail:
                raise Conflict(
                    f"Payment {payment.number} is already applied to invoice "
                    f"{invoice.number}. Reverse that allocation before applying it again.",
                    details={"payment_id": str(payment_id), "invoice_id": str(invoice_id)},
                ) from exc
            if "over-allocated" in detail:
                raise Conflict(
                    f"Invoice {invoice.number} is already settled in full. "
                    "Another payment was applied to it a moment ago.",
                    details={"invoice_id": str(invoice_id)},
                ) from exc
            raise

        if post:
            # Applying an existing advance converts a liability into revenue
            # settlement; recording it with the payment does not, because the
            # payment entry already carried both sides.
            policy = await self._policy_for(organization_id, date.today())
            rule = await self._posting.resolve_policy_rule(
                organization_id=organization_id,
                policy_id=policy.id,
                operation_code=OPERATION_ADVANCE_APPLIED,
            )
            await self._posting.post(
                organization_id=organization_id,
                request=PostingRequest(
                    entry_date=date.today(),
                    description=(f"Advance from {payment.number} applied to {invoice.number}"),
                    source_kind=SourceKind.PAYMENT,
                    # The allocation's own id, not the payment/invoice pair.
                    # The pair is not unique over time: once an allocation can
                    # be reversed, the same payment may be applied to the same
                    # invoice again, and the idempotency guard would refuse the
                    # correction as a duplicate (ADR-038).
                    source_id=str(allocation.id),
                    purpose=PostingPurpose.ADJUSTMENT,
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
            events.PAYMENT_ALLOCATED,
            organization_id=organization_id,
            target_type="payment_allocation",
            target_id=allocation.id,
            target_label=f"{payment.number} -> {invoice.number}",
            business_event=f"payment:{payment.id}",
            new_values={
                "amount": amount,
                "payment_number": payment.number,
                "invoice_number": invoice.number,
            },
        )
        await self._refresh_invoice_statuses(organization_id, [invoice_id])
        return allocation

    async def _oldest_first_allocation(
        self, *, organization_id: uuid.UUID, customer_id: uuid.UUID, available: Decimal
    ) -> list[tuple[uuid.UUID, Decimal]]:
        """Spread a payment over the customer's open invoices, oldest first."""
        stmt = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.customer_id == customer_id,
                Invoice.status.in_(list(OPEN_INVOICE_STATUSES)),
            )
            .order_by(Invoice.issue_date, Invoice.number)
        )
        plan: list[tuple[uuid.UUID, Decimal]] = []
        remaining = available
        for invoice in (await self._session.execute(stmt)).scalars():
            if remaining <= ZERO:
                break
            allocated = await self.allocated_to_invoice(
                organization_id=organization_id, invoice_id=invoice.id
            )
            outstanding = quantize(invoice.total - allocated, invoice.currency)
            if outstanding <= ZERO:
                continue
            take = min(outstanding, remaining)
            plan.append((invoice.id, take))
            remaining = quantize(remaining - take, invoice.currency)
        return plan

    async def reverse_allocation(
        self,
        *,
        organization_id: uuid.UUID,
        allocation_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> PaymentAllocation:
        """Unapply a payment from an invoice, in the subledger and the ledger.

        This used to delete the row and post nothing. When the allocation was
        an advance being applied — the one case that posts an entry of its
        own — that left `6310 Dr / 4010 Cr` standing with the allocation it
        described gone: the invoice reopened in the subledger while the ledger
        went on reporting the receivable as settled. The trial balance still
        balanced, which is exactly why it went unnoticed (ADR-038).
        """
        if not reason or not reason.strip():
            raise ValidationFailed("Reversing an allocation requires a reason")
        stmt = select(PaymentAllocation).where(
            PaymentAllocation.organization_id == organization_id,
            PaymentAllocation.id == allocation_id,
        )
        allocation = (await self._session.execute(stmt)).scalar_one_or_none()
        if allocation is None:
            raise NotFound("Allocation not found")
        if allocation.reversed_at is not None:
            raise Conflict("This allocation has already been reversed")

        invoice_id = allocation.invoice_id
        await self._reverse_allocation_posting(
            organization_id=organization_id,
            allocation_id=allocation.id,
            reason=reason.strip(),
            actor_user_id=actor_user_id,
        )

        allocation.reversed_at = datetime.now(UTC)
        allocation.reversal_reason = reason.strip()
        allocation.reversed_by_user_id = actor_user_id
        await self._session.flush()

        self._audit.record(
            events.ALLOCATION_REVERSED,
            organization_id=organization_id,
            target_type="payment_allocation",
            target_id=allocation.id,
            reason=reason.strip(),
            business_event=f"payment:{allocation.payment_id}",
            old_values={"amount": allocation.amount, "invoice_id": str(invoice_id)},
            new_values={"reversed": True},
        )
        await self._refresh_invoice_statuses(organization_id, [invoice_id])
        return allocation

    async def _reverse_allocation_posting(
        self,
        *,
        organization_id: uuid.UUID,
        allocation_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        """Reverse the entry an advance application posted, if there was one.

        An allocation made at the moment the payment was recorded posts
        nothing of its own — the payment entry already carried both sides — so
        there is usually nothing here to reverse. An advance applied later
        does post, under the allocation's own id.
        """
        entries = await self._posting.find_by_source(
            organization_id=organization_id,
            source_kind=SourceKind.PAYMENT,
            source_id=str(allocation_id),
            purpose=PostingPurpose.ADJUSTMENT,
        )
        for entry in entries:
            if entry.reversed_by_entry_id is None:
                await self._posting.reverse(
                    organization_id=organization_id,
                    entry_id=entry.id,
                    reason=reason,
                    actor_user_id=actor_user_id,
                    reversal_date=date.today(),
                )

    # --------------------------------------------------------------- reads

    async def get_invoice(self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
        stmt = select(Invoice).where(
            Invoice.organization_id == organization_id, Invoice.id == invoice_id
        )
        invoice = (await self._session.execute(stmt)).scalar_one_or_none()
        if invoice is None:
            raise NotFound("Invoice not found")
        return invoice

    async def get_payment(self, *, organization_id: uuid.UUID, payment_id: uuid.UUID) -> Payment:
        stmt = select(Payment).where(
            Payment.organization_id == organization_id, Payment.id == payment_id
        )
        payment = (await self._session.execute(stmt)).scalar_one_or_none()
        if payment is None:
            raise NotFound("Payment not found")
        return payment

    async def list_settleable_payments(
        self, *, organization_id: uuid.UUID, limit: int = 200
    ) -> list[Payment]:
        """Recorded card and transfer payments a bank line could settle.

        Banking suggests matches from this list rather than querying
        `Payment` itself: which payments are still awaiting money from a
        third party is a receivables question, so it is answered here.

        Cash payments are excluded — they reach the bank as a deposit, which
        the cash module owns, not as a settlement of their own.
        """
        stmt = (
            select(Payment)
            .where(
                Payment.organization_id == organization_id,
                Payment.status == PaymentStatus.RECORDED,
                Payment.method.in_([PaymentMethod.CARD, PaymentMethod.BANK_TRANSFER]),
            )
            .order_by(Payment.received_on.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_invoices(
        self,
        *,
        organization_id: uuid.UUID,
        customer_id: uuid.UUID | None = None,
        statuses: frozenset[InvoiceStatus] | None = None,
        limit: int = 200,
    ) -> list[Invoice]:
        """A customer's bills, newest first.

        Here rather than queried directly by a caller, so that a module above
        this one — the clinic's patient page, for instance — reads invoices
        through the service that owns them instead of acquiring its own
        opinion about which statuses count.
        """
        stmt = select(Invoice).where(Invoice.organization_id == organization_id)
        if customer_id is not None:
            stmt = stmt.where(Invoice.customer_id == customer_id)
        if statuses is not None:
            stmt = stmt.where(Invoice.status.in_(list(statuses)))
        stmt = stmt.order_by(Invoice.issue_date.desc(), Invoice.number.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def lock_invoice(self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
        """Take the invoice's row lock and return it.

        The serialization point for settling one invoice. A caller that
        composes several payments into one settlement — cash, card and an
        applied advance in a single submission — must hold this lock for the
        whole of it, or two cashiers reading the same remaining balance will
        each believe their payment fits (ADR-039).

        Public because the composition happens above this module: `sales`
        cannot see a cash drawer. Everything *inside* the lock still goes
        through this service's own methods.
        """
        return await self._locked_invoice(organization_id, invoice_id)

    async def unapplied_payments(
        self, *, organization_id: uuid.UUID, customer_id: uuid.UUID
    ) -> list[tuple[Payment, Decimal]]:
        """The customer's payments that still have money spare, oldest first.

        Each pair is a payment and how much of it is unapplied — an advance
        looking for an invoice. Oldest first so the money a patient left
        longest ago is used first, which is what they expect and what keeps
        the liability from ageing.
        """
        stmt = (
            select(Payment)
            .where(
                Payment.organization_id == organization_id,
                Payment.customer_id == customer_id,
                Payment.status == PaymentStatus.RECORDED,
            )
            .order_by(Payment.received_on, Payment.number)
        )
        spare: list[tuple[Payment, Decimal]] = []
        for payment in (await self._session.execute(stmt)).scalars():
            applied = await self.allocated_from_payment(
                organization_id=organization_id, payment_id=payment.id
            )
            available = quantize(payment.amount - applied, payment.currency)
            if available > ZERO:
                spare.append((payment, available))
        return spare

    async def allocated_to_invoice_by_method(
        self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> dict[PaymentMethod, Decimal]:
        """Live allocations on this invoice, split by how the money arrived.

        The patient-facing panel shows what was paid in cash and what by
        card. Both are the same receivable settlement in the ledger, so this
        is a presentation question — but it is answered from the allocation
        rows, never from a figure anybody typed.
        """
        stmt = (
            select(Payment.method, func.coalesce(func.sum(PaymentAllocation.amount), 0))
            .select_from(PaymentAllocation)
            .join(
                Payment,
                (Payment.id == PaymentAllocation.payment_id)
                & (Payment.organization_id == PaymentAllocation.organization_id),
            )
            .where(
                PaymentAllocation.organization_id == organization_id,
                PaymentAllocation.invoice_id == invoice_id,
                PaymentAllocation.reversed_at.is_(None),
                Payment.status == PaymentStatus.RECORDED,
            )
            .group_by(Payment.method)
        )
        rows = (await self._session.execute(stmt)).all()
        return {method: quantize(Decimal(total or 0)) for method, total in rows}

    async def _locked_invoice(self, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
        stmt = (
            select(Invoice)
            .where(Invoice.organization_id == organization_id, Invoice.id == invoice_id)
            .with_for_update()
        )
        invoice = (await self._session.execute(stmt)).scalar_one_or_none()
        if invoice is None:
            raise NotFound("Invoice not found")
        return invoice

    async def _locked_payment(self, organization_id: uuid.UUID, payment_id: uuid.UUID) -> Payment:
        stmt = (
            select(Payment)
            .where(Payment.organization_id == organization_id, Payment.id == payment_id)
            .with_for_update()
        )
        payment = (await self._session.execute(stmt)).scalar_one_or_none()
        if payment is None:
            raise NotFound("Payment not found")
        return payment

    async def list_lines(
        self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> list[InvoiceLine]:
        stmt = (
            select(InvoiceLine)
            .where(
                InvoiceLine.organization_id == organization_id,
                InvoiceLine.invoice_id == invoice_id,
            )
            .order_by(InvoiceLine.line_number)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_allocations(
        self,
        *,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID | None = None,
        invoice_id: uuid.UUID | None = None,
        include_reversed: bool = False,
    ) -> list[PaymentAllocation]:
        """Allocations, live ones only unless the history is asked for.

        Every caller that sums money wants live rows. Only a history view
        wants the reversed ones, and it has to say so.
        """
        stmt = select(PaymentAllocation).where(PaymentAllocation.organization_id == organization_id)
        if payment_id is not None:
            stmt = stmt.where(PaymentAllocation.payment_id == payment_id)
        if invoice_id is not None:
            stmt = stmt.where(PaymentAllocation.invoice_id == invoice_id)
        if not include_reversed:
            stmt = stmt.where(PaymentAllocation.reversed_at.is_(None))
        return list((await self._session.execute(stmt)).scalars().all())

    async def allocated_to_invoice(
        self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(
            PaymentAllocation.organization_id == organization_id,
            PaymentAllocation.invoice_id == invoice_id,
            PaymentAllocation.reversed_at.is_(None),
        )
        return Decimal((await self._session.execute(stmt)).scalar_one() or 0)

    async def allocated_from_payment(
        self, *, organization_id: uuid.UUID, payment_id: uuid.UUID
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(
            PaymentAllocation.organization_id == organization_id,
            PaymentAllocation.payment_id == payment_id,
            PaymentAllocation.reversed_at.is_(None),
        )
        return Decimal((await self._session.execute(stmt)).scalar_one() or 0)

    async def outstanding(self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Decimal:
        invoice = await self.get_invoice(organization_id=organization_id, invoice_id=invoice_id)
        allocated = await self.allocated_to_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        return quantize(invoice.total - allocated, invoice.currency)

    async def customer_balance(
        self, *, organization_id: uuid.UUID, customer_id: uuid.UUID
    ) -> CustomerBalance:
        """What a customer owes, and what they have paid in advance."""
        invoiced = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(Invoice.total), 0)).where(
                        Invoice.organization_id == organization_id,
                        Invoice.customer_id == customer_id,
                        Invoice.status.in_(
                            [
                                InvoiceStatus.ISSUED,
                                InvoiceStatus.PARTIALLY_PAID,
                                InvoiceStatus.PAID,
                                InvoiceStatus.WRITTEN_OFF,
                            ]
                        ),
                    )
                )
            ).scalar_one()
            or 0
        )
        paid = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.organization_id == organization_id,
                        Payment.customer_id == customer_id,
                        Payment.status == PaymentStatus.RECORDED,
                    )
                )
            ).scalar_one()
            or 0
        )
        allocated = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                    .select_from(PaymentAllocation)
                    .join(
                        Payment,
                        (Payment.id == PaymentAllocation.payment_id)
                        & (Payment.organization_id == PaymentAllocation.organization_id),
                    )
                    .where(
                        PaymentAllocation.organization_id == organization_id,
                        PaymentAllocation.reversed_at.is_(None),
                        Payment.customer_id == customer_id,
                        Payment.status == PaymentStatus.RECORDED,
                    )
                )
            ).scalar_one()
            or 0
        )
        written_off = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(Invoice.total), 0)).where(
                        Invoice.organization_id == organization_id,
                        Invoice.customer_id == customer_id,
                        Invoice.status == InvoiceStatus.WRITTEN_OFF,
                    )
                )
            ).scalar_one()
            or 0
        )
        written_off_allocated = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                    .select_from(PaymentAllocation)
                    .join(
                        Invoice,
                        (Invoice.id == PaymentAllocation.invoice_id)
                        & (Invoice.organization_id == PaymentAllocation.organization_id),
                    )
                    .where(
                        PaymentAllocation.organization_id == organization_id,
                        PaymentAllocation.reversed_at.is_(None),
                        Invoice.customer_id == customer_id,
                        Invoice.status == InvoiceStatus.WRITTEN_OFF,
                    )
                )
            ).scalar_one()
            or 0
        )
        # Only the part actually given up on leaves the outstanding figure;
        # anything the patient did pay towards it already left via `allocated`.
        forgiven = written_off - written_off_allocated
        return CustomerBalance(
            customer_id=customer_id,
            invoiced=quantize(invoiced),
            paid=quantize(paid),
            outstanding=quantize(invoiced - allocated - forgiven),
            unapplied_credit=quantize(paid - allocated),
            written_off=quantize(forgiven),
        )

    # ------------------------------------------------------------ internals

    async def _policy_for(self, organization_id: uuid.UUID, on: date):
        """The accounting policy in force, with a message that says what to do."""
        try:
            return await PolicyService(self._session, self._audit).policy_in_force_on(
                organization_id=organization_id, on=on
            )
        except NotFound as exc:
            raise Conflict(
                "No accounting policy is in force, so this cannot be posted. "
                "Create and activate a policy under Accounting first.",
                details={"date": on.isoformat()},
            ) from exc

    async def _refresh_invoice_statuses(
        self, organization_id: uuid.UUID, invoice_ids: list[uuid.UUID]
    ) -> None:
        """Recompute PAID / PARTIALLY_PAID / ISSUED from the allocations.

        Status is derived, never set by hand, so it cannot disagree with the
        allocation rows it describes.
        """
        for invoice_id in {i for i in invoice_ids if i is not None}:
            invoice = await self.get_invoice(organization_id=organization_id, invoice_id=invoice_id)
            if invoice.status in (
                InvoiceStatus.DRAFT,
                InvoiceStatus.VOID,
                InvoiceStatus.CANCELLED,
                InvoiceStatus.WRITTEN_OFF,
            ):
                continue
            allocated = await self.allocated_to_invoice(
                organization_id=organization_id, invoice_id=invoice_id
            )
            if allocated >= invoice.total:
                invoice.status = InvoiceStatus.PAID
            elif allocated > ZERO:
                invoice.status = InvoiceStatus.PARTIALLY_PAID
            else:
                invoice.status = InvoiceStatus.ISSUED
        await self._session.flush()
