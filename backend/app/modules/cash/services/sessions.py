"""Cash registers, sessions and the daily close.

The specification's worked example drives this file: a patient pays 300 000
in cash, the system records it against the right receivable and raises
expected cash; at the end of the day an authorised cashier enters the
physically counted amount; the system compares and records any difference.

Three rules hold that together:

* **Expected cash is computed, never stored.** It is the sum of the session's
  movements. There is no field anybody can edit into agreement with a count.
* **A discrepancy is recorded, not absorbed.** It needs a reason and, unless
  the organization owner is acting, approval by somebody other than the
  cashier who ran the session.
* **Nothing posted is deleted.** A mistaken movement is corrected with an
  offsetting adjustment, leaving both visible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.money import ZERO, quantize
from app.core.permissions import one_person_may_do_and_approve
from app.modules.accounting.enums import PostingPurpose, SourceKind
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import (
    PostingLine,
    PostingRequest,
    PostingService,
)
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.cash.enums import (
    INFLOW_KINDS,
    OUTFLOW_KINDS,
    DepositRoute,
    MovementKind,
    SessionStatus,
)
from app.modules.cash.models import CashMovement, CashRegister, CashSession

#: Operation codes this module posts under. The accounts behind them are the
#: accounting policy's business, not this module's.
OPERATION_DEPOSIT = {
    DepositRoute.STAFF: "cash_deposit_via_staff",
    DepositRoute.COLLECTION: "cash_deposit_via_collection",
}
OPERATION_CASH_EXPENSE = "cash_expense"
OPERATION_SHORTAGE = "cash_shortage"
OPERATION_OVERAGE = "cash_overage"


@dataclass(frozen=True, slots=True)
class SessionTotals:
    """What the drawer should hold, and how that figure was reached."""

    opening_float: Decimal
    receipts: Decimal
    refunds: Decimal
    expenses: Decimal
    deposits: Decimal
    adjustments_in: Decimal
    adjustments_out: Decimal
    expected: Decimal
    counted: Decimal | None
    discrepancy: Decimal | None


class CashSessionService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditRecorder,
        posting: PostingService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._posting = posting or PostingService(session, audit)

    # ------------------------------------------------------------ registers

    async def list_registers(
        self, *, organization_id: uuid.UUID, active_only: bool = True
    ) -> list[CashRegister]:
        stmt = select(CashRegister).where(CashRegister.organization_id == organization_id)
        if active_only:
            stmt = stmt.where(CashRegister.is_active.is_(True))
        return list((await self._session.execute(stmt.order_by(CashRegister.code))).scalars().all())

    async def get_register(
        self, *, organization_id: uuid.UUID, register_id: uuid.UUID
    ) -> CashRegister:
        stmt = select(CashRegister).where(
            CashRegister.organization_id == organization_id,
            CashRegister.id == register_id,
        )
        register = (await self._session.execute(stmt)).scalar_one_or_none()
        if register is None:
            raise NotFound("Cash register not found")
        return register

    async def create_register(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        account_code: str,
        currency: str = "UZS",
    ) -> CashRegister:
        from app.modules.accounting.services.accounts import AccountService

        accounts = AccountService(self._session, self._audit)
        account = await accounts.get_by_code(organization_id=organization_id, code=account_code)
        if account is None:
            raise NotFound(f"Account {account_code!r} does not exist")
        if not account.is_postable:
            raise ValidationFailed(
                f"Account {account_code} is a {account.level.value.lower()} and "
                "cannot hold a till's balance"
            )

        existing = (
            await self._session.execute(
                select(CashRegister).where(
                    CashRegister.organization_id == organization_id,
                    CashRegister.code == code,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"A cash register with code {code!r} already exists")

        register = CashRegister(
            organization_id=organization_id,
            code=code.strip(),
            name=name.strip(),
            account_id=account.id,
            currency=currency.upper(),
        )
        self._session.add(register)
        await self._session.flush()
        self._audit.record(
            "cash.register.created",
            organization_id=organization_id,
            target_type="cash_register",
            target_id=register.id,
            target_label=code,
            new_values={"code": code, "name": name, "account": account_code},
        )
        return register

    # ------------------------------------------------------------- sessions

    async def open_session(
        self,
        *,
        organization_id: uuid.UUID,
        register_id: uuid.UUID,
        business_date: date,
        opening_float: Decimal = ZERO,
        actor_user_id: uuid.UUID | None = None,
    ) -> CashSession:
        """Open a till for the day, with whatever float is already in it.

        The opening float is recorded as a movement rather than a column, so
        the expected balance has a single derivation.
        """
        register = await self.get_register(organization_id=organization_id, register_id=register_id)
        opening_float = quantize(opening_float, register.currency)
        if opening_float < ZERO:
            raise ValidationFailed("An opening float cannot be negative")

        await self._check_opening_float(
            organization_id=organization_id,
            register=register,
            opening_float=opening_float,
        )

        session = CashSession(
            organization_id=organization_id,
            register_id=register_id,
            business_date=business_date,
            status=SessionStatus.OPEN,
            currency=register.currency,
            opened_at=datetime.now(UTC),
            opened_by_user_id=actor_user_id,
        )
        self._session.add(session)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # A partial unique index allows one open session per register.
            if "uq_cash_session_one_open_per_register" not in str(exc.orig):
                raise
            raise Conflict(
                f"Register {register.code} already has an open session. "
                "Close it before opening another.",
                details={"register_code": register.code},
            ) from exc

        if opening_float > ZERO:
            self._session.add(
                CashMovement(
                    organization_id=organization_id,
                    session_id=session.id,
                    kind=MovementKind.OPENING_FLOAT,
                    amount=opening_float,
                    description="Opening float",
                    occurred_at=session.opened_at,
                    recorded_by_user_id=actor_user_id,
                )
            )
            await self._session.flush()

        self._audit.record(
            events.CASH_SESSION_OPENED,
            organization_id=organization_id,
            target_type="cash_session",
            target_id=session.id,
            target_label=f"{register.code} {business_date.isoformat()}",
            new_values={
                "register": register.code,
                "business_date": business_date,
                "opening_float": opening_float,
            },
        )
        return session

    async def _check_opening_float(
        self,
        *,
        organization_id: uuid.UUID,
        register: CashRegister,
        opening_float: Decimal,
    ) -> None:
        """Keep the drawer and the ledger telling the same story.

        An opening float counts toward expected cash but is not posted — the
        money is already there. That only holds if it is genuinely already
        there *in the ledger too*, so:

        * after a previous session, the float must equal what that session
          counted. The cash did not move overnight, and letting it differ
          would silently reintroduce a discrepancy somebody already explained;
        * on a register's first session, it must equal the ledger balance of
          the register's account. A larger float is an opening balance, which
          is an accounting decision needing a journal entry (see
          docs/OPEN_QUESTIONS.md question A6) — not something typed into a
          till.

        Without this, expected cash and the ledger's cash account disagree by
        the float and nothing notices. That is exactly what happened the
        first time this module was driven end to end.
        """
        previous = (
            await self._session.execute(
                select(CashSession)
                .where(
                    CashSession.organization_id == organization_id,
                    CashSession.register_id == register.id,
                    CashSession.status == SessionStatus.CLOSED,
                )
                .order_by(CashSession.closed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if previous is not None:
            carried = quantize(previous.counted_amount or ZERO, register.currency)
            if opening_float != carried:
                raise ValidationFailed(
                    f"The previous session on {register.code} closed with "
                    f"{carried} counted. The opening float must match it — "
                    "the cash did not move between sessions.",
                    details={
                        "expected_float": str(carried),
                        "requested": str(opening_float),
                        "previous_session_id": str(previous.id),
                    },
                )
            return

        from app.modules.accounting.services.accounts import AccountService

        ledger_balance = quantize(
            await AccountService(self._session, self._audit).balance(
                organization_id=organization_id, account_id=register.account_id
            ),
            register.currency,
        )
        if opening_float != ledger_balance:
            raise ValidationFailed(
                f"This is the first session on {register.code}, and the ledger "
                f"shows {ledger_balance} in its cash account. An opening float "
                f"of {opening_float} would make the drawer and the ledger "
                "disagree. Post an opening balance journal entry first.",
                details={
                    "ledger_balance": str(ledger_balance),
                    "requested": str(opening_float),
                    "account_id": str(register.account_id),
                },
            )

    async def get_session(
        self, *, organization_id: uuid.UUID, session_id: uuid.UUID
    ) -> CashSession:
        stmt = select(CashSession).where(
            CashSession.organization_id == organization_id,
            CashSession.id == session_id,
        )
        session = (await self._session.execute(stmt)).scalar_one_or_none()
        if session is None:
            raise NotFound("Cash session not found")
        return session

    async def open_session_for(
        self, *, organization_id: uuid.UUID, register_id: uuid.UUID
    ) -> CashSession | None:
        stmt = select(CashSession).where(
            CashSession.organization_id == organization_id,
            CashSession.register_id == register_id,
            CashSession.status == SessionStatus.OPEN,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_sessions(
        self,
        *,
        organization_id: uuid.UUID,
        register_id: uuid.UUID | None = None,
        status: SessionStatus | None = None,
        limit: int = 50,
    ) -> list[CashSession]:
        stmt = select(CashSession).where(CashSession.organization_id == organization_id)
        if register_id is not None:
            stmt = stmt.where(CashSession.register_id == register_id)
        if status is not None:
            stmt = stmt.where(CashSession.status == status)
        stmt = stmt.order_by(CashSession.business_date.desc(), CashSession.opened_at.desc()).limit(
            limit
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------ movements

    async def list_movements(
        self, *, organization_id: uuid.UUID, session_id: uuid.UUID
    ) -> list[CashMovement]:
        stmt = (
            select(CashMovement)
            .where(
                CashMovement.organization_id == organization_id,
                CashMovement.session_id == session_id,
            )
            .order_by(CashMovement.occurred_at, CashMovement.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_deposits(
        self, *, organization_id: uuid.UUID, limit: int = 200
    ) -> list[CashMovement]:
        """Every deposit sent to the bank, newest first.

        Banking reconciles statement lines against these and reads them
        through here rather than through `CashMovement`: a deposit's route
        decides which transit account a confirmation clears, and that is this
        module's rule to own.
        """
        stmt = (
            select(CashMovement)
            .where(
                CashMovement.organization_id == organization_id,
                CashMovement.kind == MovementKind.DEPOSIT,
            )
            .order_by(CashMovement.occurred_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_deposit(
        self, *, organization_id: uuid.UUID, movement_id: uuid.UUID
    ) -> CashMovement:
        """One deposit, refusing any other kind of movement by name."""
        stmt = select(CashMovement).where(
            CashMovement.organization_id == organization_id,
            CashMovement.id == movement_id,
        )
        movement = (await self._session.execute(stmt)).scalar_one_or_none()
        if movement is None:
            raise NotFound("Cash deposit not found")
        if movement.kind is not MovementKind.DEPOSIT:
            raise ValidationFailed("Only a cash deposit can be matched to a bank line")
        return movement

    async def totals(self, *, organization_id: uuid.UUID, session_id: uuid.UUID) -> SessionTotals:
        """Expected cash, derived from the movements. Never read from a column."""
        session = await self.get_session(organization_id=organization_id, session_id=session_id)
        rows = (
            await self._session.execute(
                select(CashMovement.kind, func.coalesce(func.sum(CashMovement.amount), 0))
                .where(
                    CashMovement.organization_id == organization_id,
                    CashMovement.session_id == session_id,
                )
                .group_by(CashMovement.kind)
            )
        ).all()
        by_kind = {kind: Decimal(total) for kind, total in rows}

        def amount(kind: MovementKind) -> Decimal:
            return quantize(by_kind.get(kind, ZERO), session.currency)

        inflow = sum((amount(k) for k in INFLOW_KINDS), ZERO)
        outflow = sum((amount(k) for k in OUTFLOW_KINDS), ZERO)
        expected = quantize(inflow - outflow, session.currency)

        return SessionTotals(
            opening_float=amount(MovementKind.OPENING_FLOAT),
            receipts=amount(MovementKind.RECEIPT),
            refunds=amount(MovementKind.REFUND),
            expenses=amount(MovementKind.EXPENSE),
            deposits=amount(MovementKind.DEPOSIT),
            adjustments_in=amount(MovementKind.ADJUSTMENT_IN),
            adjustments_out=amount(MovementKind.ADJUSTMENT_OUT),
            expected=expected,
            counted=session.counted_amount,
            discrepancy=session.discrepancy,
        )

    async def _require_open(self, organization_id: uuid.UUID, session_id: uuid.UUID) -> CashSession:
        session = await self.get_session(organization_id=organization_id, session_id=session_id)
        if session.status is not SessionStatus.OPEN:
            raise Conflict(
                f"This cash session is {session.status.value.lower()} and cannot "
                "take further movements",
                details={"status": session.status.value},
            )
        return session

    async def record_payment_receipt(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        payment_id: uuid.UUID,
        amount: Decimal,
        description: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> CashMovement:
        """Record that a customer payment went into this drawer.

        The payment itself is created and posted by `sales`; this only
        records that the cash physically arrived here, which is what makes it
        part of the expected balance. No ledger entry is made — the payment
        already posted one.
        """
        session = await self._require_open(organization_id, session_id)
        movement = CashMovement(
            organization_id=organization_id,
            session_id=session_id,
            kind=MovementKind.RECEIPT,
            amount=quantize(amount, session.currency),
            description=description,
            occurred_at=datetime.now(UTC),
            payment_id=payment_id,
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(movement)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if "uq_cash_movement_payment_kind" not in str(exc.orig):
                raise
            raise Conflict(
                "This payment is already recorded in a cash session",
                details={"payment_id": str(payment_id)},
            ) from exc
        return movement

    async def record_payment_refund(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        payment_id: uuid.UUID,
        amount: Decimal,
        description: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> CashMovement:
        """Record that cash was paid back out of this drawer for a payment.

        The mirror of `record_payment_receipt`, and it exists because the
        reversal is only half the story: `sales` reverses the ledger entry,
        but the money physically leaves a till, and until this row exists the
        drawer goes on expecting it. The end-of-day count then reports a
        shortage that is not one, and real cash is posted to 9430 as a loss —
        the same failure as taking cash with no session open, in the other
        direction (ADR-040).

        **Not necessarily the session that took the money.** A refund given
        the next day comes out of the drawer that is open now, which is where
        the cash actually goes from. No ledger entry is made here either: the
        refund already posted its reversal.
        """
        session = await self._require_open(organization_id, session_id)
        movement = CashMovement(
            organization_id=organization_id,
            session_id=session_id,
            kind=MovementKind.REFUND,
            amount=quantize(amount, session.currency),
            description=description,
            occurred_at=datetime.now(UTC),
            payment_id=payment_id,
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(movement)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if "uq_cash_movement_payment_kind" not in str(exc.orig):
                raise
            raise Conflict(
                "This payment has already been refunded from a cash session",
                details={"payment_id": str(payment_id)},
            ) from exc
        return movement

    async def record_expense(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        amount: Decimal,
        description: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> CashMovement:
        """Money paid out of the till, posted as an expense."""
        session = await self._require_open(organization_id, session_id)
        amount = quantize(amount, session.currency)
        if amount <= ZERO:
            raise ValidationFailed("An expense must be a positive amount")
        if not description.strip():
            raise ValidationFailed("A cash expense must say what it was for")

        movement = CashMovement(
            organization_id=organization_id,
            session_id=session_id,
            kind=MovementKind.EXPENSE,
            amount=amount,
            description=description.strip(),
            occurred_at=datetime.now(UTC),
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(movement)
        await self._session.flush()

        await self._post(
            organization_id=organization_id,
            session=session,
            operation_code=OPERATION_CASH_EXPENSE,
            amount=amount,
            description=f"Cash expense: {description.strip()}",
            source_id=str(movement.id),
            purpose=PostingPurpose.PAYMENT,
            actor_user_id=actor_user_id,
        )
        self._audit.record(
            "cash.expense.recorded",
            organization_id=organization_id,
            target_type="cash_movement",
            target_id=movement.id,
            business_event=f"cash_session:{session_id}",
            new_values={"amount": amount, "description": description.strip()},
        )
        return movement

    async def record_deposit(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        amount: Decimal,
        route: DepositRoute,
        reference: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> CashMovement:
        """Send cash to the bank.

        Posts to a *transit* account, not to the bank account: the money has
        left the till but has not arrived at the bank, and a non-zero transit
        balance is exactly the list of deposits not yet confirmed by a
        statement.
        """
        session = await self._require_open(organization_id, session_id)
        amount = quantize(amount, session.currency)
        if amount <= ZERO:
            raise ValidationFailed("A deposit must be a positive amount")

        totals = await self.totals(organization_id=organization_id, session_id=session_id)
        if amount > totals.expected:
            raise ValidationFailed(
                f"The till is only expected to hold {totals.expected}; "
                f"{amount} cannot be deposited from it",
                details={"expected": str(totals.expected), "requested": str(amount)},
            )

        movement = CashMovement(
            organization_id=organization_id,
            session_id=session_id,
            kind=MovementKind.DEPOSIT,
            amount=amount,
            description=f"Deposit to bank ({route.value.lower()})",
            occurred_at=datetime.now(UTC),
            deposit_route=route,
            reference=reference,
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(movement)
        await self._session.flush()

        await self._post(
            organization_id=organization_id,
            session=session,
            operation_code=OPERATION_DEPOSIT[route],
            amount=amount,
            description=f"Cash deposit to bank ({route.value.lower()})",
            source_id=str(movement.id),
            purpose=PostingPurpose.PAYMENT,
            actor_user_id=actor_user_id,
        )
        self._audit.record(
            "cash.deposit.recorded",
            organization_id=organization_id,
            target_type="cash_movement",
            target_id=movement.id,
            business_event=f"cash_session:{session_id}",
            new_values={"amount": amount, "route": route.value, "reference": reference},
        )
        return movement

    # ----------------------------------------------------------- the close

    async def submit_count(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        counted_amount: Decimal,
        reason: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> CashSession:
        """Enter what was physically counted and record the difference.

        The discrepancy is stored at this moment rather than recomputed
        later, so the reconciliation records what was actually found.
        """
        session = await self._require_open(organization_id, session_id)
        counted = quantize(counted_amount, session.currency)
        if counted < ZERO:
            raise ValidationFailed("A counted amount cannot be negative")

        totals = await self.totals(organization_id=organization_id, session_id=session_id)
        discrepancy = quantize(counted - totals.expected, session.currency)

        if discrepancy != ZERO and not (reason or "").strip():
            raise ValidationFailed(
                f"The count differs from the expected balance by {discrepancy}. "
                "A discrepancy must be explained.",
                details={
                    "expected": str(totals.expected),
                    "counted": str(counted),
                    "discrepancy": str(discrepancy),
                    "field": "reason",
                },
            )

        session.counted_amount = counted
        session.counted_at = datetime.now(UTC)
        session.counted_by_user_id = actor_user_id
        session.discrepancy = discrepancy
        session.discrepancy_reason = (reason or "").strip() or None
        session.status = SessionStatus.COUNTED
        await self._session.flush()

        self._audit.record(
            events.CASH_COUNT_SUBMITTED,
            organization_id=organization_id,
            target_type="cash_session",
            target_id=session.id,
            business_event=f"cash_session:{session.id}",
            reason=session.discrepancy_reason,
            new_values={
                "expected": totals.expected,
                "counted": counted,
                "discrepancy": discrepancy,
            },
        )
        return session

    async def close_session(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_may_approve: bool = False,
        actor_is_owner: bool = False,
        duties_cannot_be_separated: bool = False,
    ) -> CashSession:
        """Close a counted session, posting any discrepancy.

        A session whose count matched closes on the cashier's own authority.
        One that did not needs an approver — and **not** the person who ran
        the session, which is the `cash.session.operate` /
        `cash.discrepancy.approve` segregation-of-duties pair. The
        organization owner is exempt, because a single-owner clinic has
        nobody else (ADR-018); the audit trail is the compensating control.

        An organization that has declared it cannot separate duties is exempt
        on the same grounds, and then `cash.session.operate` is authority
        enough: in a three-person clinic the person at the till is the only
        person there, and refusing the close does not summon a second one —
        it leaves the drawer open overnight, with the day's takings in it and
        no ledger entry for the shortage. The close is marked `self_approved`
        so the relaxation is visible in the record rather than inferred from
        an absence (ADR-041).
        """
        session = await self.get_session(organization_id=organization_id, session_id=session_id)
        if session.status is SessionStatus.CLOSED:
            raise Conflict("This cash session is already closed")
        if session.status is not SessionStatus.COUNTED:
            raise Conflict(
                "Submit a physical count before closing the session",
                details={"status": session.status.value},
            )

        discrepancy = session.discrepancy or ZERO
        self_approved = False
        if discrepancy != ZERO:
            alone = one_person_may_do_and_approve(
                duties_cannot_be_separated=duties_cannot_be_separated,
                actor_is_owner=actor_is_owner,
            )
            # Operating the till is authority enough only where there is
            # nobody else to ask; otherwise the approval permission is still
            # required, and still from a different pair of hands.
            if not (actor_may_approve or alone):
                raise PermissionDenied(
                    "Closing a session with a cash discrepancy requires approval authority"
                )
            if (
                not alone
                and actor_user_id is not None
                and actor_user_id == session.counted_by_user_id
            ):
                raise PermissionDenied(
                    "The person who counted the cash cannot also approve the "
                    "discrepancy. Ask someone else to approve it."
                )
            self_approved = (
                actor_user_id is not None and actor_user_id == session.counted_by_user_id
            )

            await self._post_discrepancy(
                organization_id=organization_id,
                session=session,
                discrepancy=discrepancy,
                actor_user_id=actor_user_id,
            )
            session.approved_by_user_id = actor_user_id
            session.approved_at = datetime.now(UTC)

        session.status = SessionStatus.CLOSED
        session.closed_at = datetime.now(UTC)
        await self._session.flush()

        self._audit.record(
            events.CASH_SESSION_CLOSED,
            organization_id=organization_id,
            target_type="cash_session",
            target_id=session.id,
            business_event=f"cash_session:{session.id}",
            reason=session.discrepancy_reason,
            new_values={
                "counted": session.counted_amount,
                "discrepancy": discrepancy,
                "approved": discrepancy != ZERO,
                # The person who counted also signed it off, because the
                # organization has declared there is nobody else. Recorded on
                # the row rather than left to be worked out from two user ids
                # that happen to match (ADR-041).
                "self_approved": self_approved,
            },
        )
        if discrepancy != ZERO:
            self._audit.record(
                events.CASH_DISCREPANCY_APPROVED,
                organization_id=organization_id,
                target_type="cash_session",
                target_id=session.id,
                reason=session.discrepancy_reason,
                new_values={"discrepancy": discrepancy},
            )
        return session

    async def _post_discrepancy(
        self,
        *,
        organization_id: uuid.UUID,
        session: CashSession,
        discrepancy: Decimal,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        """Bring the ledger into line with what was counted.

        A shortage is an expense; an overage is other operating income.
        Neither is silently absorbed into revenue.
        """
        shortage = discrepancy < ZERO
        await self._post(
            organization_id=organization_id,
            session=session,
            operation_code=OPERATION_SHORTAGE if shortage else OPERATION_OVERAGE,
            amount=abs(discrepancy),
            description=(
                f"Cash {'shortage' if shortage else 'overage'} on "
                f"{session.business_date.isoformat()}"
            ),
            source_id=str(session.id),
            purpose=PostingPurpose.ADJUSTMENT,
            actor_user_id=actor_user_id,
        )

    async def _post(
        self,
        *,
        organization_id: uuid.UUID,
        session: CashSession,
        operation_code: str,
        amount: Decimal,
        description: str,
        source_id: str,
        purpose: PostingPurpose,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        policy = await self._policy_for(organization_id, session.business_date)
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=operation_code,
        )
        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=session.business_date,
                description=description,
                source_kind=SourceKind.CASH_SESSION,
                source_id=source_id,
                purpose=purpose,
                currency=session.currency,
                operation_code=operation_code,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=amount),
                    PostingLine(account_id=rule.credit_account_id, credit=amount),
                ],
            ),
            actor_user_id=actor_user_id,
        )

    async def _policy_for(self, organization_id: uuid.UUID, on: date):
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
