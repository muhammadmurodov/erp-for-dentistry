"""Paying a supplier out of the till.

One act, two records, and they must not be able to come apart: the ledger
posting that reduces the payable, and the drawer movement that reduces the
cash. A payment that reached the ledger and not the till would leave the
evening count short by exactly the amount nobody could explain — which is
how a shortage becomes a suspicion about a person.

It lives **in cash, not in purchasing**, because cash sits above purchasing
in the module layering: a cash session contains movements that refer to
other modules' records, and the container knows its contents rather than the
other way round. `purchasing` never learns that cash sessions exist, exactly
as `sales` never does.

The same shape as `clinic/services/settlement.py`, for the same reason.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.core.money import ZERO, quantize
from app.modules.audit.service import AuditRecorder
from app.modules.cash.enums import MovementKind
from app.modules.cash.models import CashMovement
from app.modules.cash.services.sessions import CashSessionService
from app.modules.purchasing.enums import SupplierPaymentMethod
from app.modules.purchasing.services.payables import PayablesService


@dataclass(frozen=True, slots=True)
class TillSupplierPayment:
    """What paying a supplier out of the till produced.

    A summary rather than the `SupplierPayment` row: `cash` sits above
    `purchasing` and composes it, but it does not get to hand that module's
    model around — the caller asks `PayablesService` for the record it wants
    (ADR-001's boundary rule, the same one `clinic` follows with `sales`).
    """

    payment_id: uuid.UUID
    code: str
    amount: Decimal
    movement_id: uuid.UUID


class SupplierCashSettlementService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._cash = CashSessionService(session, audit)
        self._payables = PayablesService(session, audit)

    async def pay_from_till(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        supplier_id: uuid.UUID,
        amount: Decimal,
        paid_on: date | None = None,
        invoice_id: uuid.UUID | None = None,
        reference: str | None = None,
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> TillSupplierPayment:
        """Pay a supplier in cash: post it, and take it out of the drawer.

        The till is checked **before** the payment is created rather than
        after, so a closed drawer or an empty one refuses the whole act
        instead of leaving a posted payment with no movement behind it.
        """
        cash_session = await self._cash.get_session(
            organization_id=organization_id, session_id=session_id
        )
        if cash_session.status.value != "OPEN":
            raise Conflict(
                "That cash session is closed. Open the till before paying out of it.",
                details={"status": cash_session.status.value},
            )

        amount = quantize(amount, cash_session.currency)
        if amount <= ZERO:
            raise ValidationFailed("A payment must be a positive amount")

        totals = await self._cash.totals(organization_id=organization_id, session_id=session_id)
        if amount > totals.expected:
            raise ValidationFailed(
                "There is not that much in the till",
                details={"expected": str(totals.expected)},
            )

        payment = await self._payables.record_payment(
            organization_id=organization_id,
            supplier_id=supplier_id,
            amount=amount,
            method=SupplierPaymentMethod.CASH,
            paid_on=paid_on or cash_session.business_date,
            invoice_id=invoice_id,
            reference=reference,
            notes=notes,
            currency=cash_session.currency,
            actor_user_id=actor_user_id,
        )

        movement = CashMovement(
            organization_id=organization_id,
            session_id=session_id,
            kind=MovementKind.EXPENSE,
            amount=amount,
            description=f"Supplier payment {payment.code}",
            occurred_at=datetime.now(UTC),
            supplier_payment_id=payment.id,
            reference=reference,
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(movement)
        await self._session.flush()

        self._audit.record(
            "cash.supplier_payment.recorded",
            organization_id=organization_id,
            target_type="cash_movement",
            target_id=movement.id,
            target_label=payment.code,
            business_event=f"supplier_payment:{payment.id}",
            actor_user_id=actor_user_id,
            new_values={"amount": amount, "session_id": str(session_id)},
        )
        return TillSupplierPayment(
            payment_id=payment.id,
            code=payment.code,
            amount=amount,
            movement_id=movement.id,
        )

    async def movement_for(
        self, *, organization_id: uuid.UUID, supplier_payment_id: uuid.UUID
    ) -> CashMovement | None:
        """The drawer movement a supplier payment produced, if any."""
        stmt = select(CashMovement).where(
            CashMovement.organization_id == organization_id,
            CashMovement.supplier_payment_id == supplier_payment_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
