"""Phase 2 end to end over HTTP: chart → calendar → posting → trial balance.

This is integration test 5 from the specification — journal posting through
to a financial statement — exercised the way a user would reach it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import events
from app.modules.audit.models import AuditLog

pytestmark = pytest.mark.integration

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"
API = "/api/v1/accounting"


class TestAccountingSlice:
    async def test_full_flow(self, client: AsyncClient, login, org_a, db: AsyncSession) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)

        # 1. Import the statutory chart of accounts.
        seeded = await client.post(
            f"{API}/accounts/seed-statutory-chart",
            headers=headers,
            json={"confirm": True},
        )
        assert seeded.status_code == 200, seeded.text
        summary = seeded.json()
        assert summary["accounts"] > 300
        assert summary["postable"] > 250
        assert summary["structural"] > 50
        # The notice must travel with the data: this classification is derived.
        assert "needing confirmation" in summary["notice"]

        # 2. Accounts are marked provisional, and structural rows are not postable.
        accounts = await client.get(f"{API}/accounts", headers=headers, params={"search": "5010"})
        assert accounts.status_code == 200
        cash = next(a for a in accounts.json() if a["code"] == "5010")
        assert cash["is_postable"] is True
        assert cash["needs_confirmation"] is True

        groups = await client.get(f"{API}/accounts", headers=headers, params={"search": "5000"})
        group = next(a for a in groups.json() if a["code"] == "5000")
        assert group["is_postable"] is False
        assert group["level"] == "GROUP"

        # 3. Open a fiscal year; monthly periods come with it.
        year = await client.post(
            f"{API}/fiscal-years",
            headers=headers,
            json={
                "code": "2026",
                "starts_on": "2026-01-01",
                "ends_on": "2026-12-31",
            },
        )
        assert year.status_code == 201, year.text

        periods = await client.get(f"{API}/periods", headers=headers)
        assert periods.status_code == 200
        assert len(periods.json()) == 12
        assert all(p["status"] == "OPEN" for p in periods.json())

        # 4. A manual entry, posted immediately.
        posted = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-10-15",
                "description": "Cash payment for dental treatment",
                "reason": "Opening the books with a cash sale",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5010", "debit": "300000"},
                    {"account_code": "9030", "credit": "300000"},
                ],
            },
        )
        assert posted.status_code == 201, posted.text
        entry = posted.json()
        assert entry["status"] == "POSTED"
        assert entry["entry_number"] == "JE-000001"
        assert entry["total"] == "300000.00"
        assert len(entry["lines"]) == 2
        assert {line["account_code"] for line in entry["lines"]} == {"5010", "9030"}

        # 5. An unbalanced entry is refused, naming the difference.
        unbalanced = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-10-15",
                "description": "Does not balance",
                "reason": "testing",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5010", "debit": "300000"},
                    {"account_code": "9030", "credit": "250000"},
                ],
            },
        )
        assert unbalanced.status_code == 422
        assert unbalanced.json()["code"] == "unbalanced_entry"
        assert unbalanced.json()["details"]["difference"] == "50000.00"

        # 6. Posting to a structural account is refused.
        to_group = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-10-15",
                "description": "Posting to a group",
                "reason": "testing",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5000", "debit": "100"},
                    {"account_code": "9030", "credit": "100"},
                ],
            },
        )
        assert to_group.status_code == 422
        assert "cannot be posted to" in str(to_group.json()["details"])

        # 7. A draft, then posted separately.
        draft = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-10-20",
                "description": "Bank commission for October",
                "reason": "Monthly bank charge",
                "lines": [
                    {"account_code": "9420", "debit": "5000"},
                    {"account_code": "5110", "credit": "5000"},
                ],
            },
        )
        assert draft.status_code == 201
        assert draft.json()["status"] == "DRAFT"
        draft_id = draft.json()["id"]

        # A draft moves no balance yet.
        mid = await client.get(
            f"{API}/reports/trial-balance",
            headers=headers,
            params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        assert all(r["account_code"] != "9420" for r in mid.json()["rows"])

        promoted = await client.post(f"{API}/journal/{draft_id}/post", headers=headers)
        assert promoted.status_code == 200
        assert promoted.json()["status"] == "POSTED"

        # 8. The trial balance balances.
        tb = await client.get(
            f"{API}/reports/trial-balance",
            headers=headers,
            params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        assert tb.status_code == 200
        report = tb.json()
        assert report["is_balanced"] is True
        assert report["total_period_debit"] == report["total_period_credit"]
        assert report["total_period_debit"] == "305000.00"

        rows = {r["account_code"]: r for r in report["rows"]}
        assert rows["5010"]["closing_debit"] == "300000.00"
        assert rows["9030"]["closing_credit"] == "300000.00"
        assert rows["9420"]["closing_debit"] == "5000.00"
        assert rows["5110"]["closing_credit"] == "5000.00"

        # 9. An account statement shows the running balance.
        cash_id = rows["5010"]["account_id"]
        statement = await client.get(
            f"{API}/reports/account-statement/{cash_id}",
            headers=headers,
            params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        assert statement.status_code == 200
        assert statement.json()["closing_balance"] == "300000.00"
        assert statement.json()["lines"][0]["running_balance"] == "300000.00"

        # 10. Reversal, with a reason; the original stays and nets to zero.
        reversal = await client.post(
            f"{API}/journal/{entry['id']}/reverse",
            headers=headers,
            json={
                "reason": "Treatment cancelled and the payment refunded",
                "reversal_date": "2026-10-25",
            },
        )
        assert reversal.status_code == 200, reversal.text
        assert reversal.json()["reversal_of_entry_id"] == entry["id"]

        original = await client.get(f"{API}/journal/{entry['id']}", headers=headers)
        assert original.json()["status"] == "POSTED"
        assert original.json()["reversed_by_entry_id"] == reversal.json()["id"]

        after = await client.get(
            f"{API}/reports/trial-balance",
            headers=headers,
            params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        rows_after = {r["account_code"]: r for r in after.json()["rows"]}
        # Money is always presented at the currency's scale, so a nil balance
        # reads as "0.00" rather than "0".
        assert rows_after["5010"]["closing_debit"] == "0.00"
        assert rows_after["5010"]["closing_credit"] == "0.00"
        assert after.json()["is_balanced"] is True

        # 11. Reversing twice is refused.
        again = await client.post(
            f"{API}/journal/{entry['id']}/reverse",
            headers=headers,
            json={"reason": "second attempt"},
        )
        assert again.status_code == 409

        # 12. A posted entry cannot be deleted.
        deleted = await client.delete(f"{API}/journal/{entry['id']}", headers=headers)
        assert deleted.status_code == 409
        assert "Reverse it instead" in deleted.json()["message"]

        # 13. Every material step is in the audit trail.
        actions = set(
            (
                await db.execute(
                    select(AuditLog.action).where(AuditLog.organization_id == org_a.id)
                )
            ).scalars()
        )
        for required in (
            "accounting.chart.seeded",
            "accounting.fiscal_year.created",
            events.JOURNAL_ENTRY_CREATED,
            events.JOURNAL_ENTRY_POSTED,
            events.JOURNAL_ENTRY_REVERSED,
        ):
            assert required in actions, f"missing audit action {required}"

    async def test_period_close_blocks_posting_then_reopen_restores_it(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.post(
            f"{API}/accounts/seed-statutory-chart",
            headers=headers,
            json={"confirm": True},
        )
        await client.post(
            f"{API}/fiscal-years",
            headers=headers,
            json={"code": "2026", "starts_on": "2026-01-01", "ends_on": "2026-12-31"},
        )
        periods = (await client.get(f"{API}/periods", headers=headers)).json()
        january = next(p for p in periods if p["code"] == "2026-01")

        closed = await client.post(f"{API}/periods/{january['id']}/close", headers=headers)
        assert closed.status_code == 200
        assert closed.json()["status"] == "CLOSED"

        refused = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-01-15",
                "description": "Into a closed period",
                "reason": "testing",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5010", "debit": "1000"},
                    {"account_code": "9030", "credit": "1000"},
                ],
            },
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "period_closed"

        reopened = await client.post(
            f"{API}/periods/{january['id']}/reopen",
            headers=headers,
            json={"reason": "A supplier invoice arrived after the close"},
        )
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "OPEN"
        assert reopened.json()["reopened_reason"]

        accepted = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-01-15",
                "description": "Posted after reopening",
                "reason": "late invoice",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5010", "debit": "1000"},
                    {"account_code": "9030", "credit": "1000"},
                ],
            },
        )
        assert accepted.status_code == 201

    async def test_reopening_without_a_reason_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.post(
            f"{API}/fiscal-years",
            headers=headers,
            json={"code": "2026", "starts_on": "2026-01-01", "ends_on": "2026-12-31"},
        )
        periods = (await client.get(f"{API}/periods", headers=headers)).json()
        january = next(p for p in periods if p["code"] == "2026-01")
        await client.post(f"{API}/periods/{january['id']}/close", headers=headers)

        response = await client.post(
            f"{API}/periods/{january['id']}/reopen",
            headers=headers,
            json={"reason": ""},
        )
        assert response.status_code == 422


class TestAccountingAuthorization:
    async def test_a_cashier_cannot_read_or_post_to_the_ledger(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(f"cashier@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        assert (await client.get(f"{API}/accounts", headers=headers)).status_code == 403
        assert (await client.get(f"{API}/journal", headers=headers)).status_code == 403

    async def test_an_accountant_may_post_but_not_change_policy(
        self, client: AsyncClient, login, org_a
    ) -> None:
        owner = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.post(
            f"{API}/accounts/seed-statutory-chart", headers=owner, json={"confirm": True}
        )
        await client.post(
            f"{API}/fiscal-years",
            headers=owner,
            json={"code": "2026", "starts_on": "2026-01-01", "ends_on": "2026-12-31"},
        )

        accountant = await login(
            f"accountant@{org_a.organization.code}.example.com", STAFF_PASSWORD
        )
        posted = await client.post(
            f"{API}/journal",
            headers=accountant,
            json={
                "entry_date": "2026-10-15",
                "description": "Posted by the accountant",
                "reason": "routine",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5010", "debit": "1000"},
                    {"account_code": "9030", "credit": "1000"},
                ],
            },
        )
        assert posted.status_code == 201

        policy = await client.post(
            f"{API}/policies",
            headers=accountant,
            json={"name": "Attempted", "effective_from": "2026-01-01"},
        )
        assert policy.status_code == 403

    async def test_an_auditor_can_read_reports_but_not_post(
        self, client: AsyncClient, login, org_a
    ) -> None:
        owner = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.post(
            f"{API}/fiscal-years",
            headers=owner,
            json={"code": "2026", "starts_on": "2026-01-01", "ends_on": "2026-12-31"},
        )

        auditor = await login(f"auditor@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        tb = await client.get(
            f"{API}/reports/trial-balance",
            headers=auditor,
            params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        assert tb.status_code == 200

        attempt = await client.post(
            f"{API}/journal",
            headers=auditor,
            json={
                "entry_date": "2026-10-15",
                "description": "Auditor should not be able to do this",
                "reason": "testing",
                "lines": [
                    {"account_code": "5010", "debit": "1"},
                    {"account_code": "9030", "credit": "1"},
                ],
            },
        )
        assert attempt.status_code == 403


class TestMonetaryPrecisionOverHttp:
    async def test_amounts_travel_as_strings(self, client: AsyncClient, login, org_a) -> None:
        """A JSON number would be a float to most clients and lose precision."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.post(
            f"{API}/accounts/seed-statutory-chart", headers=headers, json={"confirm": True}
        )
        await client.post(
            f"{API}/fiscal-years",
            headers=headers,
            json={"code": "2026", "starts_on": "2026-01-01", "ends_on": "2026-12-31"},
        )
        posted = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-10-15",
                "description": "Awkward amount",
                "reason": "precision check",
                "post_immediately": True,
                "lines": [
                    {"account_code": "5010", "debit": "17020.65"},
                    {"account_code": "9030", "credit": "17020.65"},
                ],
            },
        )
        assert posted.status_code == 201
        body = posted.json()
        assert body["total"] == "17020.65"
        assert isinstance(body["lines"][0]["debit"], str)
        assert Decimal(body["lines"][0]["debit"]) == Decimal("17020.65")

    async def test_a_json_number_amount_is_refused(self, client: AsyncClient, login, org_a) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.post(
            f"{API}/journal",
            headers=headers,
            json={
                "entry_date": "2026-10-15",
                "description": "Float amount",
                "reason": "precision check",
                "lines": [
                    {"account_code": "5010", "debit": 17020.65},
                    {"account_code": "9030", "credit": 17020.65},
                ],
            },
        )
        assert response.status_code == 422
        assert "not JSON numbers" in str(response.json()["details"])
