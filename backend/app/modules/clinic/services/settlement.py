"""Settling a patient's bill: what is still owed, and taking it.

Two things live here, and the first is the more important.

**The remaining balance is computed, never stored and never typed.**

    remaining = invoice total - live allocations

Live allocations are the confirmed money: an advance that has been applied,
a cash payment, a card payment. A reversed allocation is not live, so
unwinding one puts the debt back by itself. There is no column anywhere that
caches this figure, which is why no screen, import or correction can leave a
patient's debt disagreeing with the payments behind it. The frontend displays
this number read-only because the frontend is not where it is decided — and
the backend would refuse an edited one anyway.

**A settlement is one submission, not several payments.**

A patient paying 400 000 in cash and 600 000 by card has made one decision
and expects one receipt. Underneath it is still two payment records with two
different settlement paths — cash into a drawer that gets counted, card into
transit until the bank settles it — because that is what the money actually
does. This module composes them into one atomic operation, under the
invoice's row lock, with the total checked against the remaining balance
before anything is taken.

It can do that because `clinic` sits above both `sales` and `cash`, so it can
reach a drawer and a receivable in one transaction. Neither of them acquires
a dependency on the other, and neither acquires a second opinion about how
much a patient has paid: every amount here is read from `BillingService` and
every entry is posted by it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.core.money import ZERO, quantize
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.cash.enums import SessionStatus
from app.modules.cash.services.sessions import CashSessionService
from app.modules.clinic.enums import SettlementStatus
from app.modules.clinic.models import SettlementRequest
from app.modules.sales.enums import OPEN_INVOICE_STATUSES, InvoiceStatus, PaymentMethod
from app.modules.sales.services.billing import BillingService

#: Invoice statuses that carry no receivable: a draft was never issued, a
#: cancelled draft never will be, and a void invoice has been reversed out.
#: None of them is "paid", which is why they have a status of their own.
_NOT_PAYABLE = frozenset({InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED, InvoiceStatus.VOID})


@dataclass(frozen=True, slots=True)
class SettlementView:
    """Everything the payment panel shows, all of it derived.

    The figures a cashier sees and the figures the backend enforces are the
    same figures, read the same way, so a screen cannot offer a payment the
    backend will refuse.
    """

    invoice_id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    currency: str
    invoice_status: InvoiceStatus
    status: SettlementStatus

    #: The price of the treatment, from the invoice lines.
    treatment_total: Decimal
    #: Confirmed money on this invoice: applied advances and allocated
    #: payments together. Unconfirmed money does not exist — a payment is
    #: either recorded and posted, or it is not a payment.
    paid: Decimal
    paid_cash: Decimal
    paid_card: Decimal
    paid_other: Decimal
    #: `treatment_total - paid`, floored at zero. It cannot go negative:
    #: the service, and a database trigger behind it, refuse the allocation
    #: that would take it there.
    remaining: Decimal
    #: Money this patient has already left with the clinic and not yet used.
    advances_available: Decimal
    #: The most this submission may take. Zero once the bill is settled.
    max_payable: Decimal

    #: False when the invoice is settled, written off, or not a receivable at
    #: all. The panel disables its inputs on this; the backend does not trust
    #: that it did.
    accepts_payment: bool
    #: Cash needs an open drawer, or it would debit 5010 without ever
    #: reaching one. None means the cash field is unavailable, not that cash
    #: is refused as a method.
    open_cash_session_id: uuid.UUID | None

    written_off: Decimal = ZERO

    @property
    def is_fully_paid(self) -> bool:
        return self.status is SettlementStatus.FULLY_PAID


@dataclass(frozen=True, slots=True)
class SettlementResult:
    """What one submission did, and where the bill stands afterwards."""

    view: SettlementView
    applied_advances: Decimal
    cash_payment_id: uuid.UUID | None
    card_payment_id: uuid.UUID | None
    #: True when this submission had already been carried out and this is the
    #: original outcome being reported again, not a second settlement.
    replayed: bool = False
    advance_payment_ids: list[uuid.UUID] = field(default_factory=list)


class SettlementService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._billing = BillingService(session, audit)
        self._cash = CashSessionService(session, audit)

    # ------------------------------------------------------------- reading

    async def view(self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> SettlementView:
        """The state of one bill. Every figure recomputed from the records."""
        invoice = await self._billing.get_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        paid = await self._billing.allocated_to_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        by_method = await self._billing.allocated_to_invoice_by_method(
            organization_id=organization_id, invoice_id=invoice_id
        )
        currency = invoice.currency
        total = quantize(invoice.total, currency)
        # Floored rather than clamped silently: if this were ever negative it
        # would mean an overpayment got past both the service check and the
        # database trigger, so the figure is floored for display and the
        # ledger is where the discrepancy would be found.
        unsettled = max(ZERO, quantize(total - paid, currency))

        if invoice.status is InvoiceStatus.WRITTEN_OFF:
            status = SettlementStatus.WRITTEN_OFF
        elif invoice.status in _NOT_PAYABLE:
            status = SettlementStatus.NOT_PAYABLE
        elif unsettled <= ZERO:
            status = SettlementStatus.FULLY_PAID
        elif paid > ZERO:
            status = SettlementStatus.PARTIALLY_PAID
        else:
            status = SettlementStatus.UNPAID

        # `remaining` is what the patient still owes **and can be asked for**,
        # which is not the same as the part of the invoice nobody has paid.
        # A draft was never billed, a voided invoice has been reversed out,
        # and a written-off debt has been given up on: in each case the
        # patient owes nothing collectible, and reporting the unpaid portion
        # here put the full price in a column headed "remaining balance" on
        # bills that nobody may pay. Found by probing the read model against
        # a voided and a written-off invoice.
        collectible = invoice.status in OPEN_INVOICE_STATUSES
        remaining = unsettled if collectible else ZERO
        accepts = collectible and remaining > ZERO
        balance = await self._billing.customer_balance(
            organization_id=organization_id, customer_id=invoice.customer_id
        )
        return SettlementView(
            invoice_id=invoice.id,
            invoice_number=invoice.number,
            customer_id=invoice.customer_id,
            currency=currency,
            invoice_status=invoice.status,
            status=status,
            treatment_total=total,
            paid=quantize(paid, currency),
            paid_cash=by_method.get(PaymentMethod.CASH, ZERO),
            paid_card=by_method.get(PaymentMethod.CARD, ZERO),
            paid_other=by_method.get(PaymentMethod.BANK_TRANSFER, ZERO),
            remaining=remaining,
            advances_available=max(ZERO, balance.unapplied_credit),
            max_payable=remaining if accepts else ZERO,
            accepts_payment=accepts,
            open_cash_session_id=await self._open_cash_session(organization_id),
            # The part given up on, not the whole invoice: anything the
            # patient did pay towards it was collected, and `customer_balance`
            # computes the forgiven figure the same way.
            written_off=unsettled if invoice.status is InvoiceStatus.WRITTEN_OFF else ZERO,
        )

    async def _open_cash_session(self, organization_id: uuid.UUID) -> uuid.UUID | None:
        """The drawer that is open, if one is.

        A clinic this size runs one register. If a second is ever open the
        earliest is offered, and the caller can always name the session it
        means explicitly.
        """
        sessions = await self._cash.list_sessions(
            organization_id=organization_id, status=SessionStatus.OPEN
        )
        return sessions[0].id if sessions else None

    # ------------------------------------------------------------- settling

    async def settle(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        request_key: str,
        cash: Decimal = ZERO,
        card: Decimal = ZERO,
        advances: Decimal = ZERO,
        received_on: date | None = None,
        cash_session_id: uuid.UUID | None = None,
        card_reference: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> SettlementResult:
        """Take a payment: cash, card, an applied advance, or any mixture.

        The sequence matters, and each step is there for a reason a defect
        taught somebody:

        1. **Claim the request key.** Before any money moves, so that a
           retried or double-clicked submission waits for the first to finish
           and is then answered with its result rather than taking the money
           again.
        2. **Lock the invoice.** Everything after this is serialized against
           any other settlement of the same bill.
        3. **Check the total against the remaining balance.** Once, for the
           whole submission. Checking each part separately would let 600 000
           cash and 600 000 card both pass on a 1 000 000 bill.
        4. **Apply the advance, then the cash, then the card.** Money the
           patient already left is used before money taken today.

        Overpayment is refused here, and refused again by a trigger on the
        allocation table that re-sums under the same locks. The frontend
        disables the inputs as well, but that is a courtesy, not a control.
        """
        cash = quantize(cash or ZERO)
        card = quantize(card or ZERO)
        advances = quantize(advances or ZERO)
        if cash < ZERO or card < ZERO or advances < ZERO:
            raise ValidationFailed("A payment amount cannot be negative")
        requested = quantize(cash + card + advances)
        if requested <= ZERO:
            raise ValidationFailed("Enter an amount in cash, in card, or an advance to apply")
        if not request_key or not request_key.strip():
            raise ValidationFailed("A settlement submission needs a request key")
        request_key = request_key.strip()
        on = received_on or date.today()

        # 1 — claim the key.
        reservation, earlier = await self._claim(
            organization_id=organization_id,
            invoice_id=invoice_id,
            request_key=request_key,
            cash=cash,
            card=card,
            advances=advances,
            actor_user_id=actor_user_id,
        )
        if earlier is not None:
            return SettlementResult(
                view=await self.view(
                    organization_id=organization_id, invoice_id=earlier.invoice_id
                ),
                applied_advances=earlier.applied_advances or ZERO,
                cash_payment_id=earlier.cash_payment_id,
                card_payment_id=earlier.card_payment_id,
                replayed=True,
            )

        # 2 — lock the bill.
        invoice = await self._billing.lock_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        if invoice.status is InvoiceStatus.WRITTEN_OFF:
            raise Conflict(
                f"Invoice {invoice.number} has been written off. "
                "Reverse the write-off before taking money against it.",
                details={"status": invoice.status.value},
            )
        if invoice.status is InvoiceStatus.PAID:
            # Said plainly, because this is the message a cashier sees when
            # a colleague has just taken the money at the other till.
            raise Conflict(
                f"Invoice {invoice.number} is already paid in full",
                details={"status": invoice.status.value, "remaining": "0"},
            )
        if invoice.status not in OPEN_INVOICE_STATUSES:
            raise Conflict(
                f"Invoice {invoice.number} is {invoice.status.value.lower()} "
                "and cannot take a payment",
                details={"status": invoice.status.value},
            )

        # 3 — one check, for the whole submission.
        allocated = await self._billing.allocated_to_invoice(
            organization_id=organization_id, invoice_id=invoice_id
        )
        remaining = quantize(invoice.total - allocated, invoice.currency)
        if remaining <= ZERO:
            raise Conflict(
                f"Invoice {invoice.number} is already paid in full",
                details={"remaining": "0"},
            )
        if requested > remaining:
            raise ValidationFailed(
                f"That is more than the {remaining} still owed on invoice "
                f"{invoice.number}. Nothing was taken.",
                details={
                    "remaining": str(remaining),
                    "requested": str(requested),
                    "cash": str(cash),
                    "card": str(card),
                    "advances": str(advances),
                },
            )

        # 4 — apply, oldest money first.
        applied_advances = ZERO
        advance_ids: list[uuid.UUID] = []
        if advances > ZERO:
            applied_advances, advance_ids = await self._apply_advances(
                organization_id=organization_id,
                invoice_id=invoice_id,
                customer_id=invoice.customer_id,
                amount=advances,
                actor_user_id=actor_user_id,
            )

        cash_payment_id = None
        if cash > ZERO:
            cash_payment_id = await self._take_cash(
                organization_id=organization_id,
                invoice=invoice,
                amount=cash,
                received_on=on,
                cash_session_id=cash_session_id,
                actor_user_id=actor_user_id,
            )

        card_payment_id = None
        if card > ZERO:
            payment = await self._billing.record_payment(
                organization_id=organization_id,
                customer_id=invoice.customer_id,
                amount=card,
                method=PaymentMethod.CARD,
                received_on=on,
                currency=invoice.currency,
                reference=card_reference,
                allocate_to=[(invoice_id, card)],
                actor_user_id=actor_user_id,
            )
            card_payment_id = payment.id

        view = await self.view(organization_id=organization_id, invoice_id=invoice_id)

        reservation.cash_payment_id = cash_payment_id
        reservation.card_payment_id = card_payment_id
        reservation.applied_advances = applied_advances
        reservation.remaining_after = view.remaining
        await self._session.flush()

        self._audit.record(
            events.INVOICE_SETTLED,
            organization_id=organization_id,
            target_type="invoice",
            target_id=invoice.id,
            target_label=invoice.number,
            business_event=f"invoice:{invoice.id}",
            actor_user_id=actor_user_id,
            new_values={
                "cash": cash,
                "card": card,
                "advances_applied": applied_advances,
                "remaining": view.remaining,
                "status": view.status.value,
            },
        )
        return SettlementResult(
            view=view,
            applied_advances=applied_advances,
            cash_payment_id=cash_payment_id,
            card_payment_id=card_payment_id,
            advance_payment_ids=advance_ids,
        )

    # ------------------------------------------------------------ internals

    async def _claim(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        request_key: str,
        cash: Decimal,
        card: Decimal,
        advances: Decimal,
        actor_user_id: uuid.UUID | None,
    ) -> tuple[SettlementRequest, SettlementRequest | None]:
        """Reserve the submission, or find the one that already holds the key.

        The unique index does the work. A duplicate arriving while the first
        submission is still open **blocks** on it rather than reading a stale
        balance, then finds the committed row and replays it.

        A key reused for a different submission is refused rather than
        replayed: it is far likelier to be a client bug than a retry, and
        answering it with somebody else's receipt would be worse than
        failing.
        """
        reservation = SettlementRequest(
            organization_id=organization_id,
            invoice_id=invoice_id,
            request_key=request_key,
            requested_cash=cash,
            requested_card=card,
            requested_advances=advances,
            submitted_by_user_id=actor_user_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(reservation)
                await self._session.flush()
        except IntegrityError as exc:
            if "uq_settlement_request_key" not in str(exc.orig):
                raise
            # The savepoint rollback usually detaches the object already.
            # If it did not, drop it, so a later flush does not retry the
            # insert that the unique index has just refused.
            if reservation in self._session:
                self._session.expunge(reservation)
            earlier = (
                await self._session.execute(
                    select(SettlementRequest).where(
                        SettlementRequest.organization_id == organization_id,
                        SettlementRequest.request_key == request_key,
                    )
                )
            ).scalar_one_or_none()
            if earlier is None:  # pragma: no cover — the index said otherwise
                raise Conflict("That submission is being processed") from exc
            same = (
                earlier.invoice_id == invoice_id
                and quantize(earlier.requested_cash) == cash
                and quantize(earlier.requested_card) == card
                and quantize(earlier.requested_advances) == advances
            )
            if not same:
                raise Conflict(
                    "That submission reference has already been used for a "
                    "different payment. Reload the invoice and try again.",
                    details={"request_key": request_key},
                ) from exc
            return reservation, earlier
        return reservation, None

    async def _apply_advances(
        self,
        *,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        customer_id: uuid.UUID,
        amount: Decimal,
        actor_user_id: uuid.UUID | None,
    ) -> tuple[Decimal, list[uuid.UUID]]:
        """Use money the patient already left, oldest payment first.

        Each application posts `6310 Dr / 4010 Cr` through the sales service:
        a liability the clinic owed becomes a receivable it has settled. The
        posting is the sales module's, under the allocation's own id, so
        reversing one later reverses its entry with it.
        """
        spare = await self._billing.unapplied_payments(
            organization_id=organization_id, customer_id=customer_id
        )
        available = quantize(sum((amount for _, amount in spare), start=ZERO))
        if amount > available:
            raise ValidationFailed(
                f"This patient has {available} in advances, not {amount}",
                details={"available": str(available), "requested": str(amount)},
            )

        used = ZERO
        touched: list[uuid.UUID] = []
        for payment, payment_available in spare:
            if used >= amount:
                break
            take = min(payment_available, quantize(amount - used))
            if take <= ZERO:
                continue
            await self._billing.allocate(
                organization_id=organization_id,
                payment_id=payment.id,
                invoice_id=invoice_id,
                amount=take,
                actor_user_id=actor_user_id,
                post=True,
            )
            used = quantize(used + take)
            touched.append(payment.id)
        return used, touched

    async def _take_cash(
        self,
        *,
        organization_id: uuid.UUID,
        invoice,
        amount: Decimal,
        received_on: date,
        cash_session_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """Take cash into an open drawer, and record that it arrived there.

        Both halves or neither. A cash payment recorded without the drawer
        movement debits 5010 in the ledger while the register's expected
        balance knows nothing about it, so the end-of-day count reports a
        shortage and real money is posted to 9430 as a loss (C-3).
        """
        session_id = cash_session_id or await self._open_cash_session(organization_id)
        if session_id is None:
            raise Conflict(
                "No cash session is open, so cash cannot be taken. Open the "
                "register first, or take this payment by card.",
                details={"method": PaymentMethod.CASH.value},
            )
        session = await self._cash.get_session(
            organization_id=organization_id, session_id=session_id
        )
        if session.status is not SessionStatus.OPEN:
            raise Conflict(
                f"That cash session is {session.status.value.lower()} and cannot take a payment",
                details={"status": session.status.value},
            )
        if session.currency != invoice.currency:
            raise ValidationFailed(
                "The drawer and the invoice are in different currencies",
                details={
                    "session_currency": session.currency,
                    "invoice_currency": invoice.currency,
                },
            )
        payment = await self._billing.record_payment(
            organization_id=organization_id,
            customer_id=invoice.customer_id,
            amount=amount,
            method=PaymentMethod.CASH,
            received_on=received_on,
            currency=invoice.currency,
            allocate_to=[(invoice.id, amount)],
            actor_user_id=actor_user_id,
        )
        await self._cash.record_payment_receipt(
            organization_id=organization_id,
            session_id=session_id,
            payment_id=payment.id,
            amount=amount,
            description=f"Payment {payment.number} for {invoice.number}",
            actor_user_id=actor_user_id,
        )
        return payment.id

    # --------------------------------------------------------------- reads

    async def history(self, *, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> list[dict]:
        """Every allocation ever made against this bill, reversals included.

        Corrections stay visible. A reversed allocation is marked, not
        deleted, so the panel can show that money was applied and then taken
        back off — which is the difference between a correction and a figure
        that changed without explanation.
        """
        allocations = await self._billing.list_allocations(
            organization_id=organization_id,
            invoice_id=invoice_id,
            include_reversed=True,
        )
        rows: list[dict] = []
        for allocation in sorted(allocations, key=lambda a: a.created_at):
            payment = await self._billing.get_payment(
                organization_id=organization_id, payment_id=allocation.payment_id
            )
            rows.append(
                {
                    "allocation_id": allocation.id,
                    "payment_id": payment.id,
                    "payment_number": payment.number,
                    "method": payment.method,
                    "amount": allocation.amount,
                    "received_on": payment.received_on,
                    "reversed_at": allocation.reversed_at,
                    "reversal_reason": allocation.reversal_reason,
                }
            )
        return rows
