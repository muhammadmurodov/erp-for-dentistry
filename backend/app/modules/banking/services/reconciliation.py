"""Matching bank statement lines to what the business already recorded.

The rule the specification is emphatic about, and the reason this module
exists: a patient payment, a fiscal receipt, a card settlement, a statement
line and a journal entry are **stages of one economic transaction**. Counting
each as revenue would multiply turnover severalfold.

So matching records a *reconciliation fact*. It posts an entry only where one
is genuinely due — moving money out of a transit account into the bank — and
never recognises income a second time.

Matching supports one-to-one, one-to-many and many-to-one, because the
clinic's existing workbook already needs all three: one settlement routinely
covers a day's card takings.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO, allocate, quantize
from app.modules.accounting.enums import PostingPurpose, SourceKind
from app.modules.accounting.services.policy import PolicyService
from app.modules.accounting.services.posting import (
    PostingLine,
    PostingRequest,
    PostingService,
)
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.banking.enums import (
    MatchableKind,
    TransactionDirection,
    TransactionStatus,
)
from app.modules.banking.models import BankTransaction, ReconciliationMatch
from app.modules.cash.enums import DepositRoute
from app.modules.cash.services.sessions import CashSessionService
from app.modules.sales.enums import PaymentMethod, PaymentStatus
from app.modules.sales.services.billing import BillingService

#: Confirming a deposit moves it out of the transit account it was posted to.
OPERATION_DEPOSIT_CONFIRMED = {
    DepositRoute.STAFF: "bank_deposit_confirmed_staff",
    DepositRoute.COLLECTION: "bank_deposit_confirmed_collection",
}
OPERATION_CARD_SETTLEMENT = "card_settlement_received"
OPERATION_BANK_COMMISSION = "bank_commission"


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """An internal record a bank line might account for."""

    kind: MatchableKind
    id: uuid.UUID
    label: str
    occurred_on: date
    amount: Decimal
    already_matched: Decimal
    outstanding: Decimal


@dataclass(frozen=True, slots=True)
class MatchRequest:
    kind: MatchableKind
    id: uuid.UUID
    amount: Decimal


class ReconciliationService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditRecorder,
        posting: PostingService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._posting = posting or PostingService(session, audit)
        # Sales owns payments and cash owns deposits. Reading them through
        # their services keeps each module's invariants where they belong;
        # the boundary test in `tests/test_module_boundaries.py` enforces it.
        self._billing = BillingService(session, audit, self._posting)
        self._cash = CashSessionService(session, audit, self._posting)

    # ------------------------------------------------------------- reading

    async def get_transaction(
        self, *, organization_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> BankTransaction:
        stmt = select(BankTransaction).where(
            BankTransaction.organization_id == organization_id,
            BankTransaction.id == transaction_id,
        )
        transaction = (await self._session.execute(stmt)).scalar_one_or_none()
        if transaction is None:
            raise NotFound("Bank transaction not found")
        return transaction

    async def list_transactions(
        self,
        *,
        organization_id: uuid.UUID,
        bank_account_id: uuid.UUID | None = None,
        status: TransactionStatus | None = None,
        limit: int = 100,
    ) -> list[BankTransaction]:
        stmt = select(BankTransaction).where(BankTransaction.organization_id == organization_id)
        if bank_account_id is not None:
            stmt = stmt.where(BankTransaction.bank_account_id == bank_account_id)
        if status is not None:
            stmt = stmt.where(BankTransaction.status == status)
        stmt = stmt.order_by(
            BankTransaction.value_date.desc(), BankTransaction.created_at.desc()
        ).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def matched_amount(
        self, *, organization_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(ReconciliationMatch.amount), 0)).where(
            ReconciliationMatch.organization_id == organization_id,
            ReconciliationMatch.bank_transaction_id == transaction_id,
        )
        return Decimal((await self._session.execute(stmt)).scalar_one() or 0)

    async def list_matches(
        self, *, organization_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> list[ReconciliationMatch]:
        stmt = (
            select(ReconciliationMatch)
            .where(
                ReconciliationMatch.organization_id == organization_id,
                ReconciliationMatch.bank_transaction_id == transaction_id,
            )
            .order_by(ReconciliationMatch.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _matched_against_source(
        self, organization_id: uuid.UUID, kind: MatchableKind, source_id: uuid.UUID
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(ReconciliationMatch.amount), 0)).where(
            ReconciliationMatch.organization_id == organization_id,
            ReconciliationMatch.source_kind == kind,
            ReconciliationMatch.source_id == source_id,
        )
        return Decimal((await self._session.execute(stmt)).scalar_one() or 0)

    # ---------------------------------------------------------- candidates

    async def candidates_for(
        self, *, organization_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> list[MatchCandidate]:
        """Internal records a bank line could plausibly account for.

        Only inflows are suggested: an outgoing line is a supplier payment or
        a tax payment, which Phase 4 handles. Suggesting nothing is better
        than suggesting the wrong thing.
        """
        transaction = await self.get_transaction(
            organization_id=organization_id, transaction_id=transaction_id
        )
        if transaction.direction is not TransactionDirection.IN:
            return []

        candidates: list[MatchCandidate] = []

        deposits = await self._cash.list_deposits(organization_id=organization_id)
        for deposit in deposits:
            already = await self._matched_against_source(
                organization_id, MatchableKind.CASH_DEPOSIT, deposit.id
            )
            outstanding = quantize(deposit.amount - already)
            if outstanding > ZERO:
                candidates.append(
                    MatchCandidate(
                        kind=MatchableKind.CASH_DEPOSIT,
                        id=deposit.id,
                        label=deposit.description or "Cash deposit",
                        occurred_on=deposit.occurred_at.date(),
                        amount=quantize(deposit.amount),
                        already_matched=quantize(already),
                        outstanding=outstanding,
                    )
                )

        payments = await self._billing.list_settleable_payments(organization_id=organization_id)
        for payment in payments:
            kind = (
                MatchableKind.CARD_PAYMENT
                if payment.method is PaymentMethod.CARD
                else MatchableKind.BANK_PAYMENT
            )
            already = await self._matched_against_source(organization_id, kind, payment.id)
            outstanding = quantize(payment.amount - already)
            if outstanding > ZERO:
                candidates.append(
                    MatchCandidate(
                        kind=kind,
                        id=payment.id,
                        label=f"{payment.number} ({payment.method.value.lower()})",
                        occurred_on=payment.received_on,
                        amount=quantize(payment.amount),
                        already_matched=quantize(already),
                        outstanding=outstanding,
                    )
                )

        # Nearest in time to the statement line first: a settlement usually
        # follows its takings by a day or two.
        candidates.sort(key=lambda c: abs((c.occurred_on - transaction.value_date).days))
        return candidates

    # ------------------------------------------------------------ matching

    async def match(
        self,
        *,
        organization_id: uuid.UUID,
        transaction_id: uuid.UUID,
        requests: list[MatchRequest],
        commission: Decimal = ZERO,
        note: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> BankTransaction:
        """Match a statement line to the records that account for it.

        `commission` is what the acquirer or bank withheld. The worked
        example from the specification: a 500 000 card payment settles as
        495 000 with 5 000 retained, so the gross takings, the settlement and
        the fee are each represented — rather than grossing up from the net
        receipt and never recording the fee, as the legacy workbook does.
        """
        transaction = await self.get_transaction(
            organization_id=organization_id, transaction_id=transaction_id
        )
        if transaction.status is TransactionStatus.IGNORED:
            raise Conflict("This transaction was set aside; un-ignore it first")
        if not requests:
            raise ValidationFailed("A match needs at least one internal record")

        commission = quantize(commission, transaction.currency)
        if commission < ZERO:
            raise ValidationFailed("Commission cannot be negative")
        if commission > ZERO and any(r.kind is not MatchableKind.CARD_PAYMENT for r in requests):
            # The workbook establishes a fee treatment for card settlements
            # (Dr 9420 / Cr 5110.03) and for nothing else. A bank's
            # cash-handling charge would need its own account pair, which is
            # the accountant's decision, not this module's.
            raise ValidationFailed(
                "A withheld fee is only supported on a card settlement. For a "
                "bank charge on a cash deposit or an incoming transfer, post "
                "the charge as a separate expense entry until the treatment "
                "is agreed (docs/OPEN_QUESTIONS.md question B4).",
            )

        pairs = [(r.kind, r.id) for r in requests]
        if len(set(pairs)) != len(pairs):
            # Two shares of one record in a single call would be refused by
            # the unique index mid-flush, which aborts the transaction rather
            # than refusing politely. One line per record, with its own
            # amount.
            raise ValidationFailed("The same record is named twice in this match")

        for request in requests:
            await self._refuse_if_already_matched(
                organization_id=organization_id,
                transaction_id=transaction_id,
                request=request,
            )

        total = quantize(
            sum((quantize(r.amount, transaction.currency) for r in requests), ZERO),
            transaction.currency,
        )
        already = await self.matched_amount(
            organization_id=organization_id, transaction_id=transaction_id
        )
        # The commission already recorded on this line, not just the one
        # passed now: a second match against the same settlement must not
        # forget the fee the first one accounted for.
        commission_total = quantize(
            transaction.commission_amount + commission, transaction.currency
        )

        # The internal records, net of the fee, must account for the bank
        # line exactly. A match that does not balance is a guess, and a
        # guess recorded as reconciled is worse than an unmatched line.
        accounted = quantize(already + total - commission_total, transaction.currency)
        if accounted > transaction.amount:
            raise ValidationFailed(
                f"These records come to {quantize(already + total, transaction.currency)} "
                f"gross, less {commission_total} withheld, which accounts for "
                f"{accounted} — but the statement line is {transaction.amount}",
                details={
                    "statement_amount": str(transaction.amount),
                    "accounted": str(accounted),
                    "commission": str(commission_total),
                },
            )

        # Each record bears its share of the fee, so what settles into the
        # bank is the net — which is what the statement line actually shows.
        shares = (
            allocate(
                commission,
                [quantize(r.amount, transaction.currency) for r in requests],
                transaction.currency,
            )
            if commission > ZERO
            else [ZERO] * len(requests)
        )

        for request, fee_share in zip(requests, shares, strict=True):
            await self._validate_source(
                organization_id=organization_id,
                request=request,
                currency=transaction.currency,
            )
            match = ReconciliationMatch(
                organization_id=organization_id,
                bank_transaction_id=transaction_id,
                source_kind=request.kind,
                source_id=request.id,
                amount=quantize(request.amount, transaction.currency),
                note=note,
                matched_by_user_id=actor_user_id,
            )
            self._session.add(match)
            try:
                await self._session.flush()
            except IntegrityError as exc:
                if "uq_reconciliation_match_pair" not in str(exc.orig):
                    raise
                raise Conflict(
                    "That record is already matched to this statement line",
                    details={
                        "source_kind": request.kind.value,
                        "source_id": str(request.id),
                    },
                ) from exc

            await self._post_for(
                organization_id=organization_id,
                transaction=transaction,
                request=request,
                fee_share=fee_share,
                actor_user_id=actor_user_id,
            )

        if commission > ZERO:
            await self._post_commission(
                organization_id=organization_id,
                transaction=transaction,
                commission=commission,
                running_total=commission_total,
                actor_user_id=actor_user_id,
            )
            transaction.commission_amount = commission_total

        # An unmatched line is **never** marked reconciled. It becomes
        # MATCHED only when the whole amount is accounted for.
        final = quantize(
            await self.matched_amount(
                organization_id=organization_id, transaction_id=transaction_id
            )
            - transaction.commission_amount,
            transaction.currency,
        )
        if final == transaction.amount:
            transaction.status = TransactionStatus.MATCHED
        await self._session.flush()

        self._audit.record(
            events.RECONCILIATION_MATCHED,
            organization_id=organization_id,
            target_type="bank_transaction",
            target_id=transaction.id,
            target_label=f"{transaction.value_date.isoformat()} {transaction.amount}",
            business_event=f"bank_transaction:{transaction.id}",
            reason=note,
            new_values={
                "statement_amount": transaction.amount,
                "matched": final,
                "commission": transaction.commission_amount,
                "status": transaction.status.value,
                "records": [f"{r.kind.value}:{r.id}" for r in requests],
            },
        )
        return transaction

    async def _refuse_if_already_matched(
        self,
        *,
        organization_id: uuid.UUID,
        transaction_id: uuid.UUID,
        request: MatchRequest,
    ) -> None:
        """Refuse the pair before inserting it.

        `uq_reconciliation_match_pair` catches this too, but an
        `IntegrityError` aborts the transaction, so a refusal raised from the
        flush leaves the caller with nothing it can continue from — and a
        many-to-one match is several inserts. Asking first keeps the refusal
        ordinary; the unique index stays as the backstop for two matches
        racing each other.
        """
        stmt = select(ReconciliationMatch.id).where(
            ReconciliationMatch.organization_id == organization_id,
            ReconciliationMatch.bank_transaction_id == transaction_id,
            ReconciliationMatch.source_kind == request.kind,
            ReconciliationMatch.source_id == request.id,
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is not None:
            raise Conflict(
                "That record is already matched to this statement line",
                details={
                    "source_kind": request.kind.value,
                    "source_id": str(request.id),
                },
            )

    async def _validate_source(
        self,
        *,
        organization_id: uuid.UUID,
        request: MatchRequest,
        currency: str,
    ) -> None:
        amount = quantize(request.amount, currency)
        if amount <= ZERO:
            raise ValidationFailed("A matched amount must be positive")

        if request.kind is MatchableKind.CASH_DEPOSIT:
            deposit = await self._cash.get_deposit(
                organization_id=organization_id, movement_id=request.id
            )
            available = quantize(deposit.amount, currency)
        else:
            payment = await self._billing.get_payment(
                organization_id=organization_id, payment_id=request.id
            )
            if payment.status is PaymentStatus.REFUNDED:
                raise ValidationFailed("A refunded payment cannot be matched")
            available = quantize(payment.amount, currency)

        already = await self._matched_against_source(organization_id, request.kind, request.id)
        if quantize(already + amount, currency) > available:
            raise ValidationFailed(
                f"That record is worth {available} and already has {already} matched against it",
                details={"available": str(available), "already": str(already)},
            )

    async def _post_for(
        self,
        *,
        organization_id: uuid.UUID,
        transaction: BankTransaction,
        request: MatchRequest,
        fee_share: Decimal,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        """Move the money out of its transit account into the bank.

        What settles is the record's gross amount less the share of the fee
        it bears, because that is what the statement line actually shows. The
        fee itself is posted once per match by `_post_commission`, and the two
        together clear the transit account to nothing.

        A bank transfer paid directly by a customer already debited the bank
        account when the payment was recorded, so matching it posts nothing —
        confirming it twice would double the bank balance.
        """
        if request.kind is MatchableKind.BANK_PAYMENT:
            return

        net = quantize(request.amount - fee_share, transaction.currency)
        if net <= ZERO:
            # The fee swallowed the whole receipt. The commission posting
            # clears the transit account on its own, and an entry for nothing
            # would only clutter the ledger.
            return

        if request.kind is MatchableKind.CASH_DEPOSIT:
            deposit = await self._cash.get_deposit(
                organization_id=organization_id, movement_id=request.id
            )
            operation = OPERATION_DEPOSIT_CONFIRMED[deposit.deposit_route or DepositRoute.STAFF]
            description = "Bank confirmed cash deposit"
        else:
            operation = OPERATION_CARD_SETTLEMENT
            description = "Card settlement received"

        await self._post(
            organization_id=organization_id,
            transaction=transaction,
            operation_code=operation,
            amount=net,
            description=description,
            source_id=f"{transaction.id}:{request.kind.value}:{request.id}",
            actor_user_id=actor_user_id,
        )

    async def _post_commission(
        self,
        *,
        organization_id: uuid.UUID,
        transaction: BankTransaction,
        commission: Decimal,
        running_total: Decimal,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        # The running total keys the posting: a retried request produces the
        # same key and is absorbed by idempotency, while a genuinely second
        # fee on the same line produces a different one and is posted. The
        # total only ever increases, so the keys cannot collide.
        await self._post(
            organization_id=organization_id,
            transaction=transaction,
            operation_code=OPERATION_BANK_COMMISSION,
            amount=commission,
            description="Bank or acquirer commission withheld",
            source_id=f"{transaction.id}:commission:{running_total}",
            actor_user_id=actor_user_id,
        )

    async def _post(
        self,
        *,
        organization_id: uuid.UUID,
        transaction: BankTransaction,
        operation_code: str,
        amount: Decimal,
        description: str,
        source_id: str,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        policy = await self._policy_for(organization_id, transaction.value_date)
        rule = await self._posting.resolve_policy_rule(
            organization_id=organization_id,
            policy_id=policy.id,
            operation_code=operation_code,
        )
        await self._posting.post(
            organization_id=organization_id,
            request=PostingRequest(
                entry_date=transaction.value_date,
                description=description,
                source_kind=SourceKind.BANK_TRANSACTION,
                source_id=source_id,
                purpose=PostingPurpose.SETTLEMENT,
                currency=transaction.currency,
                operation_code=operation_code,
                policy_id=policy.id,
                lines=[
                    PostingLine(account_id=rule.debit_account_id, debit=amount),
                    PostingLine(account_id=rule.credit_account_id, credit=amount),
                ],
            ),
            actor_user_id=actor_user_id,
        )

    async def ignore(
        self,
        *,
        organization_id: uuid.UUID,
        transaction_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> BankTransaction:
        """Set a line aside, with a reason. Never silently dropped."""
        if not reason or not reason.strip():
            raise ValidationFailed("Setting a transaction aside requires a reason")
        transaction = await self.get_transaction(
            organization_id=organization_id, transaction_id=transaction_id
        )
        if transaction.status is TransactionStatus.MATCHED:
            raise Conflict("A matched transaction cannot be set aside")
        matched = await self.matched_amount(
            organization_id=organization_id, transaction_id=transaction_id
        )
        if matched > ZERO:
            raise Conflict(
                f"{matched} is already matched to this line. Remove the "
                "matches before setting it aside.",
                details={"matched": str(matched)},
            )

        transaction.status = TransactionStatus.IGNORED
        transaction.ignored_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            "banking.transaction.ignored",
            organization_id=organization_id,
            target_type="bank_transaction",
            target_id=transaction.id,
            reason=reason.strip(),
            new_values={"status": "IGNORED", "amount": transaction.amount},
        )
        return transaction

    async def reopen(
        self,
        *,
        organization_id: uuid.UUID,
        transaction_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> BankTransaction:
        """Bring a set-aside line back into the queue."""
        transaction = await self.get_transaction(
            organization_id=organization_id, transaction_id=transaction_id
        )
        if transaction.status is not TransactionStatus.IGNORED:
            raise Conflict("Only a transaction that was set aside can be reopened")

        previous_reason = transaction.ignored_reason
        transaction.status = TransactionStatus.UNMATCHED
        transaction.ignored_reason = None
        await self._session.flush()

        self._audit.record(
            "banking.transaction.reopened",
            organization_id=organization_id,
            target_type="bank_transaction",
            target_id=transaction.id,
            old_values={"status": "IGNORED", "ignored_reason": previous_reason},
            new_values={"status": "UNMATCHED"},
        )
        return transaction

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
