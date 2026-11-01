"""An organization too small to separate duties, and what that may not do.

The clinic this is built for is run by two or three members of one family.
Segregation of duties assumes somebody else is standing there, and when
nobody is, the control does not produce a second person — it produces a
drawer that cannot be closed and, eventually, a shared login, which is the
outcome that actually destroys an audit trail (ADR-041).

So the declaration exists. These tests are mostly about its *limits*: what it
relaxes, what it leaves exactly as it was, and that every act relying on it
says so in the record.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDenied, ValidationFailed
from app.core.permissions import (
    ROLES_FOR_LARGER_ORGANIZATIONS,
    SEGREGATION_OF_DUTIES,
    check_role_permission_set,
    one_person_may_do_and_approve,
)
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.identity.service import IdentityService
from app.modules.organization.service import OrganizationService
from tests.conftest import OWNER_PASSWORD, STAFF_PASSWORD

ORG = "/api/v1/organization"
ROLES = "/api/v1/roles"
CASH = "/api/v1/cash"

YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)
BUSINESS_DATE = date(2026, 10, 1)


async def _declare(db, audit, organization_id, *, on: bool, reason: str = "three of us"):
    organization = await OrganizationService(db, audit).declare_duty_separation(
        organization_id=organization_id,
        cannot_be_separated=on,
        reason=reason,
    )
    await db.flush()
    return organization


async def _open_float(posting, sales, amount: str) -> None:
    """Put the float in the ledger first: a drawer may only hold what the
    ledger says it holds (ADR-031), so a float has to arrive by a journal
    entry, as it would at cut-over."""
    from app.modules.accounting.enums import PostingPurpose, SourceKind
    from app.modules.accounting.services.posting import PostingLine, PostingRequest

    await posting.post(
        organization_id=sales.id,
        request=PostingRequest(
            entry_date=BUSINESS_DATE,
            description="Opening cash balance",
            source_kind=SourceKind.OPENING_BALANCE,
            source_id=f"opening-cash-{amount}",
            purpose=PostingPurpose.OPENING_BALANCE,
            reason="Cut-over opening balance",
            lines=[
                PostingLine(account_code="5010", debit=Decimal(amount)),
                PostingLine(account_code="8710", credit=Decimal(amount)),
            ],
        ),
    )


async def _short_till(cash, sales, till, posting, *, counted_by, counted: str = "95000"):
    """An open session counted 5 000 short, ready for somebody to close.

    `counted_by` matters: the rule being tested is about whether the person
    who counted may also approve, so the count has to have a person on it.
    """
    await _open_float(posting, sales, "100000")
    session = await cash.open_session(
        organization_id=sales.id,
        register_id=till.id,
        business_date=BUSINESS_DATE,
        opening_float=Decimal("100000"),
    )
    await cash.submit_count(
        organization_id=sales.id,
        session_id=session.id,
        counted_amount=Decimal(counted),
        reason="Short at the end of the day; no explanation found",
        actor_user_id=counted_by,
    )
    return session


class TestTheRuleItself:
    """`one_person_may_do_and_approve` is the whole decision, in one place."""

    def test_normally_one_person_may_not_do_both(self) -> None:
        assert not one_person_may_do_and_approve(
            duties_cannot_be_separated=False, actor_is_owner=False
        )

    def test_the_owner_always_may(self) -> None:
        """ADR-018: a single-owner MChJ has nobody to delegate to."""
        assert one_person_may_do_and_approve(
            duties_cannot_be_separated=False, actor_is_owner=True
        )

    def test_a_declared_small_organization_may(self) -> None:
        assert one_person_may_do_and_approve(
            duties_cannot_be_separated=True, actor_is_owner=False
        )

    def test_the_declaration_does_not_relax_the_read_only_rule(self) -> None:
        """An auditor stays an auditor. Having few staff is not a reason to
        let the one person who may only look start writing."""
        problems = check_role_permission_set(
            "auditor",
            {"accounting.journal.post"},
            duties_cannot_be_separated=True,
        )
        assert problems, "a read-only role must still refuse write permissions"
        assert "read-only" in problems[0]

    def test_the_declaration_does_not_admit_unknown_permissions(self) -> None:
        problems = check_role_permission_set(
            "cashier", {"not.a.permission"}, duties_cannot_be_separated=True
        )
        assert problems and "Unknown permissions" in problems[0]

    def test_without_the_declaration_the_pairs_still_bite(self) -> None:
        left, right, _ = SEGREGATION_OF_DUTIES[0]
        assert check_role_permission_set("cashier", {left, right})
        assert not check_role_permission_set(
            "cashier", {left, right}, duties_cannot_be_separated=True
        )


class TestDeclaringIt:
    async def test_declaring_is_audited_with_its_reason(
        self, db: AsyncSession, audit, org_a
    ) -> None:
        organization = await _declare(
            db, audit, org_a.id, on=True, reason="two sisters and their father"
        )
        assert organization.duties_cannot_be_separated
        assert organization.duties_declared_at is not None

        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.DUTY_SEPARATION_DECLARED,
                )
            )
        ).scalar_one()
        assert row.new_values["duties_cannot_be_separated"] is True
        assert row.new_values["reason"] == "two sisters and their father"
        assert row.old_values["duties_cannot_be_separated"] is False

    async def test_withdrawing_it_clears_the_date(self, db: AsyncSession, audit, org_a) -> None:
        await _declare(db, audit, org_a.id, on=True)
        organization = await _declare(db, audit, org_a.id, on=False, reason="hired two nurses")
        assert not organization.duties_cannot_be_separated
        assert organization.duties_declared_at is None

    async def test_it_is_in_the_always_audited_set(self) -> None:
        """Weakening an approval control is not something an audit
        configuration gets to leave out."""
        from app.modules.audit.events import REQUIRED_AUDIT_ACTIONS

        assert events.DUTY_SEPARATION_DECLARED in REQUIRED_AUDIT_ACTIONS


class TestClosingATillThatCameUpShort:
    """The dead end the declaration exists to remove."""

    async def test_without_it_the_cashier_cannot_close_a_short_till(
        self, cash, sales, till, posting, org_a, db: AsyncSession
    ) -> None:
        cashier, _ = org_a.members["cashier"]
        session = await _short_till(cash, sales, till, posting, counted_by=cashier.id)
        with pytest.raises(PermissionDenied, match="approval authority"):
            await cash.close_session(
                organization_id=sales.id,
                session_id=session.id,
                actor_user_id=cashier.id,
                actor_may_approve=False,
            )

    async def test_with_it_the_cashier_closes_their_own_count(
        self, cash, sales, till, posting, audit, org_a, db: AsyncSession
    ) -> None:
        await _declare(db, audit, sales.id, on=True)
        cashier, _ = org_a.members["cashier"]
        session = await _short_till(cash, sales, till, posting, counted_by=cashier.id)
        closed = await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_user_id=session.counted_by_user_id,
            actor_may_approve=False,
            duties_cannot_be_separated=True,
        )
        await db.flush()
        assert closed.closed_at is not None

        from app.modules.accounting.services.reports import ReportService

        report = await ReportService(db).trial_balance(
            organization_id=sales.id, from_date=YEAR_START, to_date=YEAR_END
        )
        rows = {r.account_code: r for r in report.rows}
        # The shortage is still posted as an expense. Relaxing *who* may
        # approve it does not make it disappear from the ledger.
        assert rows["9430"].closing_debit == Decimal("5000.00")
        assert report.is_balanced

    async def test_a_self_approved_close_says_so_in_the_audit_trail(
        self, cash, sales, till, posting, audit, org_a, db: AsyncSession
    ) -> None:
        await _declare(db, audit, sales.id, on=True)
        cashier, _ = org_a.members["cashier"]
        session = await _short_till(cash, sales, till, posting, counted_by=cashier.id)
        await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_user_id=session.counted_by_user_id,
            duties_cannot_be_separated=True,
        )
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == sales.id,
                    AuditLog.action == events.CASH_SESSION_CLOSED,
                )
            )
        ).scalar_one()
        assert row.new_values["self_approved"] is True, (
            "the compensating control is the record saying one person did both"
        )

    async def test_a_second_person_approving_is_not_marked_self_approved(
        self, cash, sales, till, posting, audit, org_a, db: AsyncSession
    ) -> None:
        await _declare(db, audit, sales.id, on=True)
        cashier, _ = org_a.members["cashier"]
        accountant, _ = org_a.members["accountant"]
        session = await _short_till(cash, sales, till, posting, counted_by=cashier.id)
        other_person = accountant.id
        await cash.close_session(
            organization_id=sales.id,
            session_id=session.id,
            actor_user_id=other_person,
            actor_may_approve=True,
            duties_cannot_be_separated=True,
        )
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == sales.id,
                    AuditLog.action == events.CASH_SESSION_CLOSED,
                )
            )
        ).scalar_one()
        assert row.new_values["self_approved"] is False

    async def test_a_till_that_balanced_never_needed_any_of_this(
        self, cash, sales, till, posting, db: AsyncSession
    ) -> None:
        await _open_float(posting, sales, "100000")
        session = await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=BUSINESS_DATE,
            opening_float=Decimal("100000"),
        )
        await cash.submit_count(
            organization_id=sales.id,
            session_id=session.id,
            counted_amount=Decimal("100000"),
        )
        closed = await cash.close_session(organization_id=sales.id, session_id=session.id)
        assert closed.closed_at is not None


class TestCombiningResponsibilitiesOnOnePerson:
    async def test_without_the_declaration_a_conflicting_combination_is_refused(
        self, db: AsyncSession, audit, org_a
    ) -> None:
        identity = IdentityService(db, audit)
        _, membership = org_a.members["cashier"]
        with pytest.raises(ValidationFailed, match="segregation-of-duties"):
            await identity.set_membership_roles(
                membership=membership, role_codes=["cashier", "director"]
            )

    async def test_with_it_the_owner_may_combine_them(
        self, db: AsyncSession, audit, org_a
    ) -> None:
        await _declare(db, audit, org_a.id, on=True)
        identity = IdentityService(db, audit)
        _, membership = org_a.members["cashier"]
        roles = await identity.set_membership_roles(
            membership=membership, role_codes=["cashier", "director"]
        )
        assert {r.code for r in roles} == {"cashier", "director"}

    async def test_the_combination_reception_and_cashier_never_needed_it(
        self, db: AsyncSession, audit, org_a
    ) -> None:
        """Worth asserting, because it is the combination a small clinic
        actually wants and it was allowed all along: the feature is not a
        licence to assume every refusal needs a setting."""
        identity = IdentityService(db, audit)
        _, membership = org_a.members["receptionist"]
        roles = await identity.set_membership_roles(
            membership=membership, role_codes=["receptionist", "cashier", "dentist"]
        )
        assert {r.code for r in roles} == {"receptionist", "cashier", "dentist"}

    async def test_the_roles_not_worth_offering_are_named_not_guessed(self) -> None:
        assert {"director"} == ROLES_FOR_LARGER_ORGANIZATIONS


class TestOverHttp:
    """The refund gate lives in the router, so only a request exercises it.

    Phase 3c learned this the hard way: both settlement routes returned 500
    while every service-level test passed, because the fault was in the
    response, not the service.
    """

    async def test_the_declaration_is_the_owners_alone(
        self, client, login, org_a
    ) -> None:
        cashier_user, _ = org_a.members["cashier"]
        headers = await login(cashier_user.email, STAFF_PASSWORD)
        refused = await client.put(
            f"{ORG}/duty-separation",
            headers=headers,
            json={"duties_cannot_be_separated": True, "reason": "I would like this"},
        )
        assert refused.status_code == 403, refused.text

    async def test_the_owner_declares_it_and_the_record_shows_it(
        self, client, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        declared = await client.put(
            f"{ORG}/duty-separation",
            headers=headers,
            json={"duties_cannot_be_separated": True, "reason": "three of us work here"},
        )
        assert declared.status_code == 200, declared.text
        assert declared.json()["duties_cannot_be_separated"] is True
        assert declared.json()["duties_declared_at"] is not None

        # And the screens read it back from the organization, not from a flag
        # the client was told to remember.
        fetched = await client.get(ORG, headers=headers)
        assert fetched.json()["duties_cannot_be_separated"] is True

    async def test_a_declaration_without_a_reason_is_refused(
        self, client, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        refused = await client.put(
            f"{ORG}/duty-separation",
            headers=headers,
            json={"duties_cannot_be_separated": True, "reason": ""},
        )
        assert refused.status_code == 422, refused.text

    async def test_director_stops_being_offered_once_it_is_declared(
        self, client, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        before = {r["code"]: r for r in (await client.get(ROLES, headers=headers)).json()}
        assert before["director"]["offered_for_assignment"] is True

        await client.put(
            f"{ORG}/duty-separation",
            headers=headers,
            json={"duties_cannot_be_separated": True, "reason": "three of us"},
        )
        after = {r["code"]: r for r in (await client.get(ROLES, headers=headers)).json()}
        assert after["director"]["offered_for_assignment"] is False
        # Everything the clinic actually uses is still offered.
        for code in ("cashier", "receptionist", "dentist", "accountant"):
            assert after[code]["offered_for_assignment"] is True

    async def test_a_cashier_cannot_refund_their_own_takings_by_default(
        self, client, login, org_a, sales, till
    ) -> None:
        payment_id, session_id = await _take_cash_over_http(client, login, org_a, sales, till)
        cashier_user, _ = org_a.members["cashier"]
        headers = await login(cashier_user.email, STAFF_PASSWORD)
        refused = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={"payment_id": payment_id, "reason": "Rang up the wrong amount"},
        )
        assert refused.status_code == 403, refused.text
        assert "approval authority" in refused.json()["message"]

    async def test_with_the_declaration_the_cashier_may_give_it_back(
        self, client, login, org_a, sales, till
    ) -> None:
        payment_id, session_id = await _take_cash_over_http(client, login, org_a, sales, till)
        owner_headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.put(
            f"{ORG}/duty-separation",
            headers=owner_headers,
            json={"duties_cannot_be_separated": True, "reason": "three of us"},
        )

        cashier_user, _ = org_a.members["cashier"]
        headers = await login(cashier_user.email, STAFF_PASSWORD)
        refunded = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={"payment_id": payment_id, "reason": "Rang up the wrong amount"},
        )
        assert refunded.status_code == 200, refunded.text
        assert refunded.json()["status"] == "REFUNDED"

    async def test_the_declaration_does_not_let_a_receptionist_touch_the_till(
        self, client, login, org_a, sales, till
    ) -> None:
        """It relaxes *who may approve*, never *who may act*."""
        payment_id, session_id = await _take_cash_over_http(client, login, org_a, sales, till)
        owner_headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.put(
            f"{ORG}/duty-separation",
            headers=owner_headers,
            json={"duties_cannot_be_separated": True, "reason": "three of us"},
        )

        receptionist_user, _ = org_a.members["receptionist"]
        headers = await login(receptionist_user.email, STAFF_PASSWORD)
        refused = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={"payment_id": payment_id, "reason": "nothing to do with me"},
        )
        # Reception holds neither side of the refund pair, so the gate at the
        # endpoint stops the request before any of this is considered.
        assert refused.status_code == 403, refused.text


async def _take_cash_over_http(client, login, org_a, sales, till):
    """A cash payment in an open drawer, taken by the cashier.

    Taken by the cashier rather than the owner, because `self_approved` means
    the person who took the money is the person giving it back, and the test
    that cares needs those to be the same person.
    """
    cashier_user, _ = org_a.members["cashier"]
    owner_headers = await login(org_a.owner.email, OWNER_PASSWORD)
    session_id = (
        await client.post(
            f"{CASH}/sessions",
            headers=owner_headers,
            json={
                "register_id": str(till.id),
                "business_date": BUSINESS_DATE.isoformat(),
                "opening_float": "0",
            },
        )
    ).json()["id"]

    headers = await login(cashier_user.email, STAFF_PASSWORD)
    taken = await client.post(
        f"{CASH}/sessions/{session_id}/payments",
        headers=headers,
        json={
            "customer_id": str(sales.customer.id),
            "amount": "250000",
            "received_on": BUSINESS_DATE.isoformat(),
        },
    )
    assert taken.status_code == 201, taken.text
    return taken.json()["id"], session_id
