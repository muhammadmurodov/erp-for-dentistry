"""Matching bank statement lines, and the double-counting it must prevent.

`TestTheWorkedExample` is the specification's card settlement: a patient pays
500 000 by card, the acquirer withholds 5 000, and 495 000 lands in the bank.
The clinic's workbook records the 495 000 as income and loses the fee, which
both understates revenue and hides the cost. Here the gross takings, the fee
and the net settlement are three postings that reconcile to each other.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.reports import ReportService
from app.modules.banking.enums import (
    MatchableKind,
    TransactionDirection,
    TransactionStatus,
)
from app.modules.banking.models import BankTransaction
from app.modules.banking.services.reconciliation import MatchRequest
from app.modules.banking.services.statement_import import fingerprint_for
from app.modules.cash.enums import DepositRoute
from app.modules.sales.enums import PaymentMethod
from tests.cash.test_sessions import open_till

VALUE_DATE = date(2026, 10, 2)
BUSINESS_DATE = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


async def statement_line(
    db: AsyncSession,
    bank,
    *,
    amount: str,
    direction: TransactionDirection = TransactionDirection.IN,
    value_date: date = VALUE_DATE,
    description: str = "Settlement",
    document_number: str = "1",
) -> BankTransaction:
    """One imported line, as the importer would have written it."""
    transaction = BankTransaction(
        organization_id=bank.organization_id,
        bank_account_id=bank.id,
        value_date=value_date,
        direction=direction,
        amount=Decimal(amount),
        currency="UZS",
        description=description,
        document_number=document_number,
        fingerprint=fingerprint_for(
            value_date=value_date,
            direction=direction,
            amount=Decimal(amount),
            document_number=document_number,
            description=description,
        ),
    )
    db.add(transaction)
    await db.flush()
    return transaction


async def card_payment(billing, sales, *, amount: str = "500000"):
    return await billing.record_payment(
        organization_id=sales.id,
        customer_id=sales.customer.id,
        amount=Decimal(amount),
        method=PaymentMethod.CARD,
        received_on=BUSINESS_DATE,
    )


async def balance_of(db: AsyncSession, organization_id, code: str) -> Decimal:
    report = await ReportService(db).trial_balance(
        organization_id=organization_id, from_date=YEAR_START, to_date=YEAR_END
    )
    for row in report.rows:
        if row.account_code == code:
            return row.closing_debit - row.closing_credit
    return Decimal("0")


class TestTheWorkedExample:
    async def test_card_settlement_with_commission(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """500 000 taken, 5 000 withheld, 495 000 settled."""
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()

        # Taking the card payment debits the card-transit account in full.
        assert await balance_of(db, sales.id, "5110.03") == Decimal("500000")
        assert await balance_of(db, sales.id, "5110.01") == Decimal("0")

        line = await statement_line(db, bank, amount="495000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[
                MatchRequest(
                    kind=MatchableKind.CARD_PAYMENT,
                    id=payment.id,
                    amount=Decimal("500000"),
                )
            ],
            commission=Decimal("5000"),
        )
        await db.flush()

        assert line.status is TransactionStatus.MATCHED
        # The transit account is now clear: every som taken is accounted for.
        assert await balance_of(db, sales.id, "5110.03") == Decimal("0")
        assert await balance_of(db, sales.id, "5110.01") == Decimal("495000")
        # And the fee is a recorded expense rather than a silent shortfall.
        assert await balance_of(db, sales.id, "9420") == Decimal("5000")

    async def test_revenue_is_recognised_once_not_twice(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """The point of the whole module.

        The payment, the statement line and the match are three records of
        one economic event. Revenue must equal the amount invoiced, not a
        multiple of it.
        """
        from tests.sales.test_billing import issued_invoice

        invoice = await issued_invoice(sales, billing, amount="500000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=BUSINESS_DATE,
            allocate_to=[(invoice.id, Decimal("500000"))],
        )
        await db.flush()
        revenue_before = await balance_of(db, sales.id, "9030")

        line = await statement_line(db, bank, amount="495000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[
                MatchRequest(
                    kind=MatchableKind.CARD_PAYMENT,
                    id=payment.id,
                    amount=Decimal("500000"),
                )
            ],
            commission=Decimal("5000"),
        )
        await db.flush()

        assert await balance_of(db, sales.id, "9030") == revenue_before
        assert revenue_before == Decimal("-500000")  # credit balance


class TestManyToOne:
    async def test_one_settlement_covers_several_payments(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """What the clinic's workbook already does by hand each morning."""
        first = await card_payment(billing, sales, amount="300000")
        second = await card_payment(billing, sales, amount="200000")
        await db.flush()

        line = await statement_line(db, bank, amount="495000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[
                MatchRequest(MatchableKind.CARD_PAYMENT, first.id, Decimal("300000")),
                MatchRequest(MatchableKind.CARD_PAYMENT, second.id, Decimal("200000")),
            ],
            commission=Decimal("5000"),
        )
        await db.flush()

        assert line.status is TransactionStatus.MATCHED
        assert await balance_of(db, sales.id, "5110.03") == Decimal("0")
        matches = await reconciliation.list_matches(
            organization_id=sales.id, transaction_id=line.id
        )
        assert len(matches) == 2

    async def test_a_partial_match_is_not_reconciled(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """The rule the specification is explicit about: never mark an
        unmatched transaction as reconciled."""
        payment = await card_payment(billing, sales, amount="300000")
        await db.flush()

        line = await statement_line(db, bank, amount="500000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("300000"))],
        )
        await db.flush()
        assert line.status is TransactionStatus.UNMATCHED

    async def test_the_rest_can_be_matched_later(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        first = await card_payment(billing, sales, amount="300000")
        second = await card_payment(billing, sales, amount="200000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")

        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, first.id, Decimal("300000"))],
        )
        await db.flush()
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, second.id, Decimal("200000"))],
        )
        await db.flush()
        assert line.status is TransactionStatus.MATCHED

    async def test_a_second_fee_does_not_forget_the_first(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """The commission is cumulative on the line, not per request."""
        first = await card_payment(billing, sales, amount="300000")
        second = await card_payment(billing, sales, amount="200000")
        await db.flush()
        line = await statement_line(db, bank, amount="490000")

        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, first.id, Decimal("300000"))],
            commission=Decimal("5000"),
        )
        await db.flush()
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, second.id, Decimal("200000"))],
            commission=Decimal("5000"),
        )
        await db.flush()

        assert line.commission_amount == Decimal("10000.00")
        assert line.status is TransactionStatus.MATCHED
        assert await balance_of(db, sales.id, "9420") == Decimal("10000")


class TestOverMatching:
    async def test_matching_more_than_the_line_is_refused(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="100000")

        with pytest.raises(ValidationFailed, match="statement line is"):
            await reconciliation.match(
                organization_id=sales.id,
                transaction_id=line.id,
                requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
            )

    async def test_matching_more_than_the_payment_is_refused(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """A 300 000 payment cannot account for 400 000 of a bank line."""
        payment = await card_payment(billing, sales, amount="300000")
        await db.flush()
        line = await statement_line(db, bank, amount="400000")

        with pytest.raises(ValidationFailed, match="already has"):
            await reconciliation.match(
                organization_id=sales.id,
                transaction_id=line.id,
                requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("400000"))],
            )

    async def test_the_same_record_cannot_be_matched_twice(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")

        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
        )
        await db.flush()

        with pytest.raises(Conflict, match="already matched"):
            await reconciliation.match(
                organization_id=sales.id,
                transaction_id=line.id,
                requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("1"))],
            )

    async def test_one_payment_cannot_be_spread_over_two_bank_lines(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """Otherwise the same receipt would clear the transit account twice."""
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        first = await statement_line(db, bank, amount="500000", document_number="A")
        second = await statement_line(db, bank, amount="500000", document_number="B")

        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=first.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
        )
        await db.flush()

        with pytest.raises(ValidationFailed, match="already has"):
            await reconciliation.match(
                organization_id=sales.id,
                transaction_id=second.id,
                requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
            )

    async def test_a_negative_commission_is_refused(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")

        with pytest.raises(ValidationFailed, match="cannot be negative"):
            await reconciliation.match(
                organization_id=sales.id,
                transaction_id=line.id,
                requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
                commission=Decimal("-1"),
            )


class TestCashDeposits:
    async def test_confirming_a_deposit_clears_the_transit_account(
        self, db: AsyncSession, sales, billing, bank, cash, till, posting, reconciliation
    ) -> None:
        """The second half of the clinic's deposit route.

        Handing cash to a courier moves it to a transit account; it belongs
        in the bank account only once the statement proves it arrived.
        """
        session = await open_till(cash, sales, till, posting=posting, opening_float="200000")
        deposit = await cash.record_deposit(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("150000"),
            route=DepositRoute.COLLECTION,
        )
        await db.flush()
        assert await balance_of(db, sales.id, "5710.02") == Decimal("150000")

        line = await statement_line(db, bank, amount="150000", description="Inkassatsiya")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CASH_DEPOSIT, deposit.id, Decimal("150000"))],
        )
        await db.flush()

        assert line.status is TransactionStatus.MATCHED
        assert await balance_of(db, sales.id, "5710.02") == Decimal("0")
        assert await balance_of(db, sales.id, "5110.01") == Decimal("150000")

    async def test_the_staff_route_uses_its_own_transit_account(
        self, db: AsyncSession, sales, billing, bank, cash, till, posting, reconciliation
    ) -> None:
        session = await open_till(cash, sales, till, posting=posting, opening_float="200000")
        deposit = await cash.record_deposit(
            organization_id=sales.id,
            session_id=session.id,
            amount=Decimal("100000"),
            route=DepositRoute.STAFF,
        )
        await db.flush()
        assert await balance_of(db, sales.id, "5710.01") == Decimal("100000")

        line = await statement_line(db, bank, amount="100000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CASH_DEPOSIT, deposit.id, Decimal("100000"))],
        )
        await db.flush()
        assert await balance_of(db, sales.id, "5710.01") == Decimal("0")
        assert await balance_of(db, sales.id, "5710.02") == Decimal("0")


class TestBankTransfers:
    async def test_matching_a_direct_transfer_posts_nothing(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """A transfer the customer sent already debited the bank account when
        the payment was recorded. Confirming it must not debit it again."""
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("250000"),
            method=PaymentMethod.BANK_TRANSFER,
            received_on=BUSINESS_DATE,
        )
        await db.flush()
        before = await balance_of(db, sales.id, "5110.01")
        assert before == Decimal("250000")

        line = await statement_line(db, bank, amount="250000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.BANK_PAYMENT, payment.id, Decimal("250000"))],
        )
        await db.flush()

        assert line.status is TransactionStatus.MATCHED
        assert await balance_of(db, sales.id, "5110.01") == before


class TestSettingAside:
    async def test_ignoring_needs_a_reason(
        self, db: AsyncSession, sales, bank, reconciliation
    ) -> None:
        line = await statement_line(db, bank, amount="1000")
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await reconciliation.ignore(
                organization_id=sales.id, transaction_id=line.id, reason="  "
            )

    async def test_an_ignored_line_can_be_reopened(
        self, db: AsyncSession, sales, bank, reconciliation
    ) -> None:
        line = await statement_line(db, bank, amount="1000")
        await reconciliation.ignore(
            organization_id=sales.id,
            transaction_id=line.id,
            reason="Bank's own fee, posted separately",
        )
        await db.flush()
        assert line.status is TransactionStatus.IGNORED

        await reconciliation.reopen(organization_id=sales.id, transaction_id=line.id)
        await db.flush()
        assert line.status is TransactionStatus.UNMATCHED
        assert line.ignored_reason is None

    async def test_a_partially_matched_line_cannot_be_set_aside(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="100000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("100000"))],
        )
        await db.flush()

        with pytest.raises(Conflict, match="already matched to this line"):
            await reconciliation.ignore(
                organization_id=sales.id, transaction_id=line.id, reason="Not ours"
            )

    async def test_a_matched_line_cannot_be_set_aside(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
        )
        await db.flush()

        with pytest.raises(Conflict, match="cannot be set aside"):
            await reconciliation.ignore(
                organization_id=sales.id, transaction_id=line.id, reason="Mistake"
            )


class TestCandidates:
    async def test_candidates_exclude_what_is_already_matched(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        first = await card_payment(billing, sales, amount="300000")
        await card_payment(billing, sales, amount="200000")
        await db.flush()
        line = await statement_line(db, bank, amount="300000", document_number="A")
        other = await statement_line(db, bank, amount="200000", document_number="B")

        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, first.id, Decimal("300000"))],
        )
        await db.flush()

        candidates = await reconciliation.candidates_for(
            organization_id=sales.id, transaction_id=other.id
        )
        assert [c.amount for c in candidates] == [Decimal("200000.00")]

    async def test_an_outgoing_line_suggests_nothing(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        """Suggesting a receipt against a payment out would be worse than
        suggesting nothing. Supplier payments arrive in Phase 4."""
        await card_payment(billing, sales, amount="300000")
        await db.flush()
        line = await statement_line(db, bank, amount="300000", direction=TransactionDirection.OUT)
        assert (
            await reconciliation.candidates_for(organization_id=sales.id, transaction_id=line.id)
            == []
        )


class TestTenantIsolation:
    async def test_a_line_from_another_organization_is_invisible(
        self, db: AsyncSession, sales, bank, org_b, reconciliation
    ) -> None:
        line = await statement_line(db, bank, amount="1000")
        with pytest.raises(NotFound):
            await reconciliation.get_transaction(organization_id=org_b.id, transaction_id=line.id)

    async def test_a_payment_from_another_organization_cannot_be_matched(
        self, db: AsyncSession, sales, billing, bank, org_b, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")

        # Same organization for the line, a foreign id for the record: the
        # service must not find it just because the uuid is real.
        with pytest.raises(NotFound):
            await reconciliation.match(
                organization_id=org_b.id,
                transaction_id=line.id,
                requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
            )


class TestTheDatabaseEnforcesIt:
    """The service is not the only thing standing between a direct SQL
    session and an inflated turnover figure."""

    async def test_over_matching_is_refused_by_the_database(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="100000")

        await db.begin_nested()
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO reconciliation_match
                        (id, organization_id, bank_transaction_id, source_kind,
                         source_id, amount, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :txn, 'CARD_PAYMENT',
                            :src, 500000, now(), now())
                    """
                ),
                {"org": sales.id, "txn": line.id, "src": payment.id},
            )
            # Forces the deferred constraint trigger to run now.
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "is matched against it" in str(caught.value)
        await db.rollback()

    async def test_claiming_reconciled_without_matches_is_refused(
        self, db: AsyncSession, sales, bank
    ) -> None:
        """A status column cannot be set to MATCHED by wishing it."""
        line = await statement_line(db, bank, amount="100000")

        await db.begin_nested()
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            await db.execute(
                text("UPDATE bank_transaction SET status = 'MATCHED' WHERE id = :id"),
                {"id": line.id},
            )
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "marked reconciled" in str(caught.value)
        await db.rollback()

    async def test_a_fully_accounted_line_cannot_stay_unmatched(
        self, db: AsyncSession, sales, billing, bank
    ) -> None:
        """The converse: silently leaving a reconciled line in the queue
        would make the operator chase work that is already done."""
        payment = await card_payment(billing, sales, amount="100000")
        await db.flush()
        line = await statement_line(db, bank, amount="100000")

        await db.begin_nested()
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO reconciliation_match
                        (id, organization_id, bank_transaction_id, source_kind,
                         source_id, amount, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :txn, 'CARD_PAYMENT',
                            :src, 100000, now(), now())
                    """
                ),
                {"org": sales.id, "txn": line.id, "src": payment.id},
            )
            # Forces the deferred constraint trigger to run now.
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "not marked" in str(caught.value)
        await db.rollback()

    async def test_the_banks_figures_cannot_be_edited(self, db: AsyncSession, sales, bank) -> None:
        """A statement line is evidence. Revising it would make the
        reconciliation prove nothing."""
        line = await statement_line(db, bank, amount="100000")
        await db.flush()

        await db.begin_nested()
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            await db.execute(
                text("UPDATE bank_transaction SET amount = 1 WHERE id = :id"),
                {"id": line.id},
            )
            await db.flush()
        assert "cannot be edited" in str(caught.value)
        await db.rollback()

    async def test_a_match_cannot_be_re_pointed(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="500000")
        await db.flush()
        line = await statement_line(db, bank, amount="500000")
        await reconciliation.match(
            organization_id=sales.id,
            transaction_id=line.id,
            requests=[MatchRequest(MatchableKind.CARD_PAYMENT, payment.id, Decimal("500000"))],
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            await db.execute(
                text("UPDATE reconciliation_match SET amount = 1 WHERE bank_transaction_id = :id"),
                {"id": line.id},
            )
            await db.flush()
        assert "cannot be re-pointed" in str(caught.value)
        await db.rollback()

    async def test_an_ignored_line_takes_no_matches(
        self, db: AsyncSession, sales, billing, bank, reconciliation
    ) -> None:
        payment = await card_payment(billing, sales, amount="100000")
        await db.flush()
        line = await statement_line(db, bank, amount="100000")
        await reconciliation.ignore(
            organization_id=sales.id, transaction_id=line.id, reason="Not ours"
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            await db.execute(
                text(
                    """
                    INSERT INTO reconciliation_match
                        (id, organization_id, bank_transaction_id, source_kind,
                         source_id, amount, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :txn, 'CARD_PAYMENT',
                            :src, 100000, now(), now())
                    """
                ),
                {"org": sales.id, "txn": line.id, "src": payment.id},
            )
            # Forces the deferred constraint trigger to run now.
            await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "set aside" in str(caught.value)
        await db.rollback()

    async def test_a_duplicate_statement_line_is_refused(
        self, db: AsyncSession, sales, bank
    ) -> None:
        """The fingerprint's unique index, not the importer's in-memory set:
        two concurrent imports of the same file must not both win."""
        line = await statement_line(db, bank, amount="100000")
        await db.flush()

        await db.begin_nested()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    """
                    INSERT INTO bank_transaction
                        (id, organization_id, bank_account_id, value_date,
                         direction, amount, currency, status, fingerprint,
                         commission_amount, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :acct, :on, 'IN', 100000,
                            'UZS', 'UNMATCHED', :fp, 0, now(), now())
                    """
                ),
                {
                    "org": sales.id,
                    "acct": bank.id,
                    "on": VALUE_DATE,
                    "fp": line.fingerprint,
                },
            )
            await db.flush()
        await db.rollback()
