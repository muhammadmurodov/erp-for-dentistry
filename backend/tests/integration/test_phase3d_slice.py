"""Phase 3d end to end over HTTP: import a statement, match it, prove the books.

The specification's card-settlement scenario, driven the way an operator
reaches it: a patient pays 500 000 by card, the acquirer withholds 5 000, and
495 000 appears on the statement. Afterwards the card transit account must be
empty, the bank account must hold what the bank says it holds, and revenue
must still be 500 000 — not 995 000.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"
BANKING = "/api/v1/banking"
SALES = "/api/v1/sales"
ACCOUNTING = "/api/v1/accounting"

#: Two lines: the settlement to match, and a bank charge to set aside.
STATEMENT = (
    "date,document_number,description,credit,debit\n"
    "2026-10-02,101,Equiring settlement,495000,\n"
    "2026-10-03,102,Account maintenance fee,,30000\n"
)


async def balances(client: AsyncClient, headers: dict[str, str]) -> dict[str, Decimal]:
    report = await client.get(
        f"{ACCOUNTING}/reports/trial-balance",
        headers=headers,
        params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
    )
    assert report.status_code == 200, report.text
    return {
        row["account_code"]: Decimal(row["closing_debit"]) - Decimal(row["closing_credit"])
        for row in report.json()["rows"]
    }


class TestBankingSlice:
    async def test_full_flow(self, client: AsyncClient, login, org_a, sales) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)

        # 1. Pair a bank account with the ledger account it must equal.
        created = await client.post(
            f"{BANKING}/accounts",
            headers=headers,
            json={
                "code": "BANK-1",
                "name": "Settlement account",
                "account_code": "5110.01",
                "bank_name": "Demo Bank",
                "mfo": "00414",
                "account_number": "20208000900123456789",
            },
        )
        assert created.status_code == 201, created.text
        account = created.json()
        # The full number never comes back: it is a payment credential.
        assert account["account_number_masked"] == "****************6789"
        assert "20208000900123456789" not in created.text
        assert account["has_column_mapping"] is False

        # 2. The headings the importer already knows, shown before uploading.
        defaults = await client.get(f"{BANKING}/column-mapping/defaults", headers=headers)
        assert defaults.status_code == 200
        assert "value_date" in defaults.json()["aliases"]

        # 3. Take the card payment. This debits the card transit account.
        payment = await client.post(
            f"{SALES}/payments",
            headers=headers,
            json={
                "customer_id": str(sales.customer.id),
                "amount": "500000",
                "method": "CARD",
                "received_on": "2026-10-01",
            },
        )
        assert payment.status_code == 201, payment.text
        payment_id = payment.json()["id"]

        before = await balances(client, headers)
        assert before["5110.03"] == Decimal("500000")
        assert before.get("5110.01", Decimal("0")) == Decimal("0")

        # 4. Import the statement. This must post nothing.
        imported = await client.post(
            f"{BANKING}/accounts/{account['id']}/statements",
            headers=headers,
            files={"file": ("oktyabr.csv", STATEMENT.encode("utf-8"), "text/csv")},
        )
        assert imported.status_code == 201, imported.text
        result = imported.json()
        assert (result["rows_read"], result["rows_imported"]) == (2, 2)
        assert result["rows_rejected"] == 0
        assert result["period_from"] == "2026-10-02"
        assert await balances(client, headers) == before

        # 5. The queue holds both lines, unexplained.
        queue = await client.get(
            f"{BANKING}/transactions",
            headers=headers,
            params={"transaction_status": "UNMATCHED"},
        )
        assert queue.status_code == 200
        lines = queue.json()
        assert len(lines) == 2
        settlement = next(line for line in lines if line["amount"] == "495000.00")
        charge = next(line for line in lines if line["direction"] == "OUT")

        # 6. The payment is offered as what the settlement could account for.
        candidates = await client.get(
            f"{BANKING}/transactions/{settlement['id']}/candidates", headers=headers
        )
        assert candidates.status_code == 200
        offered = candidates.json()
        assert [c["id"] for c in offered] == [payment_id]
        assert offered[0]["kind"] == "CARD_PAYMENT"
        assert offered[0]["outstanding"] == "500000.00"

        # 7. Match it, declaring what the acquirer withheld.
        matched = await client.post(
            f"{BANKING}/transactions/{settlement['id']}/match",
            headers=headers,
            json={
                "records": [{"kind": "CARD_PAYMENT", "id": payment_id, "amount": "500000"}],
                "commission": "5000",
                "note": "October acquiring settlement",
            },
        )
        assert matched.status_code == 200, matched.text
        detail = matched.json()
        assert detail["status"] == "MATCHED"
        assert detail["matched_amount"] == "500000.00"
        assert detail["unaccounted_amount"] == "0.00"
        assert len(detail["matches"]) == 1

        # 8. The books. This is the point of the module.
        after = await balances(client, headers)
        # The transit account is clear: every som taken is accounted for.
        assert after["5110.03"] == Decimal("0")
        # The bank account holds exactly what the bank said it holds.
        assert after["5110.01"] == Decimal("495000")
        # The fee is a recorded expense, not a silent shortfall.
        assert after["9420"] == Decimal("5000")
        # And revenue was not recognised a second time by the settlement.
        assert after.get("9030", Decimal("0")) == before.get("9030", Decimal("0"))

        # 9. A line that is not ours to explain is set aside, with a reason.
        aside = await client.post(
            f"{BANKING}/transactions/{charge['id']}/ignore",
            headers=headers,
            json={"reason": "The bank's own fee; the accountant posts it separately"},
        )
        assert aside.status_code == 200, aside.text
        assert aside.json()["status"] == "IGNORED"

        # It is set aside, never dropped: the reason comes back with it.
        reopened = await client.post(
            f"{BANKING}/transactions/{charge['id']}/reopen", headers=headers
        )
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "UNMATCHED"
        assert reopened.json()["ignored_reason"] is None

        # 10. The import run stays visible with its counts.
        runs = await client.get(f"{BANKING}/statements", headers=headers)
        assert runs.status_code == 200
        assert runs.json()[0]["filename"] == "oktyabr.csv"
        assert runs.json()[0]["rows_imported"] == 2


class TestReimportingOverHttp:
    async def test_the_same_file_twice_adds_nothing(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        """Re-downloading a statement is routine. Doubling it is not."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        account = (
            await client.post(
                f"{BANKING}/accounts",
                headers=headers,
                json={"code": "BANK-1", "name": "Settlement", "account_code": "5110.01"},
            )
        ).json()

        for _ in range(2):
            response = await client.post(
                f"{BANKING}/accounts/{account['id']}/statements",
                headers=headers,
                files={"file": ("oktyabr.csv", STATEMENT.encode("utf-8"), "text/csv")},
            )
            assert response.status_code == 201, response.text

        assert response.json()["duplicates_skipped"] == 2
        assert response.json()["rows_imported"] == 0

        queue = await client.get(f"{BANKING}/transactions", headers=headers)
        assert len(queue.json()) == 2


class TestTheUploadItself:
    async def test_a_pdf_is_refused_by_name(self, client: AsyncClient, login, org_a, sales) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        account = (
            await client.post(
                f"{BANKING}/accounts",
                headers=headers,
                json={"code": "BANK-1", "name": "Settlement", "account_code": "5110.01"},
            )
        ).json()

        response = await client.post(
            f"{BANKING}/accounts/{account['id']}/statements",
            headers=headers,
            files={"file": ("vypiska.pdf", b"%PDF-1.7", "application/pdf")},
        )
        assert response.status_code == 422
        assert ".csv" in response.json()["message"]

    async def test_an_empty_upload_is_refused(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        account = (
            await client.post(
                f"{BANKING}/accounts",
                headers=headers,
                json={"code": "BANK-1", "name": "Settlement", "account_code": "5110.01"},
            )
        ).json()

        response = await client.post(
            f"{BANKING}/accounts/{account['id']}/statements",
            headers=headers,
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert response.status_code == 422
        assert "empty" in response.json()["message"]


class TestAuthorizationOverHttp:
    async def test_an_auditor_may_read_but_not_reconcile(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        """Reading the books and changing them are different permissions, and
        the auditor role deliberately holds only the first."""
        owner = await login(org_a.owner.email, OWNER_PASSWORD)
        account = (
            await client.post(
                f"{BANKING}/accounts",
                headers=owner,
                json={"code": "BANK-1", "name": "Settlement", "account_code": "5110.01"},
            )
        ).json()
        await client.post(
            f"{BANKING}/accounts/{account['id']}/statements",
            headers=owner,
            files={"file": ("oktyabr.csv", STATEMENT.encode("utf-8"), "text/csv")},
        )
        line = (await client.get(f"{BANKING}/transactions", headers=owner)).json()[0]

        auditor_user, _ = org_a.members["auditor"]
        auditor = await login(auditor_user.email, STAFF_PASSWORD)

        assert (await client.get(f"{BANKING}/transactions", headers=auditor)).status_code == 200
        refused = await client.post(
            f"{BANKING}/transactions/{line['id']}/ignore",
            headers=auditor,
            json={"reason": "Trying something an auditor may not do"},
        )
        assert refused.status_code == 403

    async def test_an_auditor_cannot_create_a_bank_account(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        auditor_user, _ = org_a.members["auditor"]
        auditor = await login(auditor_user.email, STAFF_PASSWORD)
        refused = await client.post(
            f"{BANKING}/accounts",
            headers=auditor,
            json={"code": "BANK-X", "name": "Nope", "account_code": "5110.01"},
        )
        assert refused.status_code == 403
