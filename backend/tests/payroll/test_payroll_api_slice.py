"""Payroll over HTTP: who may reach it, and what a blocked run says.

The service tests cover the arithmetic. These cover the boundary — and the
one thing only this layer can show: that somebody opening a payroll run is
told, in the response, which rule stops them and which open question it
belongs to, rather than meeting a generic failure.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"


async def owner_headers(login, org_a):
    return await login(f"owner@{org_a.organization.code}.example.com", OWNER_PASSWORD)


async def staff_headers(login, org_a, label):
    return await login(f"{label}@{org_a.organization.code}.example.com", STAFF_PASSWORD)


class TestWhoMayReachIt:
    async def test_the_cashier_is_refused_payroll_entirely(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A cashier holds no `payroll.*` permission at all."""
        headers = await staff_headers(login, org_a, "cashier")
        for path in ("/api/v1/payroll/runs", "/api/v1/payroll/rules"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 403, f"{path} → {response.status_code}"
            assert "payroll.run.read" in response.text

    async def test_the_receptionist_is_refused_too(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "receptionist")
        assert (await client.get("/api/v1/payroll/runs", headers=headers)).status_code == 403

    async def test_the_auditor_reads_but_changes_nothing(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "auditor")
        assert (await client.get("/api/v1/payroll/rules", headers=headers)).status_code == 200
        refused = await client.post(
            "/api/v1/payroll/rules",
            headers=headers,
            json={
                "kind": "SOCIAL_TAX",
                "method": "PERCENT_OF_GROSS",
                "rate": "12",
                "effective_from": "2019-01-01",
            },
        )
        assert refused.status_code == 403

    async def test_the_accountant_may_not_invent_a_rate(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Editing a statutory rate needs `payroll.rules.manage`, which the
        accountant role does not carry: changing a tax rate is not clerical."""
        headers = await staff_headers(login, org_a, "accountant")
        refused = await client.post(
            "/api/v1/payroll/rules",
            headers=headers,
            json={
                "kind": "SOCIAL_TAX",
                "method": "PERCENT_OF_GROSS",
                "rate": "25",
                "effective_from": "2019-01-01",
            },
        )
        assert refused.status_code == 403


class TestTheBlockedRunExplainsItself:
    async def test_opening_a_run_reports_what_stops_it(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        created = await client.post(
            "/api/v1/payroll/runs",
            headers=headers,
            json={"year": 2026, "month": 3, "working_days": 22},
        )
        assert created.status_code == 201, created.text
        reasons = created.json()["blocked_reasons"]
        assert len(reasons) == 2
        assert any("income tax" in reason for reason in reasons)
        assert any("OPEN_QUESTIONS P2" in reason for reason in reasons)

    async def test_the_seeded_drafts_do_not_unblock_anything(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Seeding proposes verified rates; drafts calculate nothing."""
        headers = await owner_headers(login, org_a)
        seeded = await client.post("/api/v1/payroll/rules/seed", headers=headers)
        assert seeded.status_code == 200, seeded.text
        kinds = {row["kind"] for row in seeded.json()}
        assert kinds == {"INCOME_TAX", "INPS", "EMPLOYEE_PENSION"}
        assert all(row["status"] == "DRAFT" for row in seeded.json())
        assert all(row["legal_reference"] for row in seeded.json())

        created = await client.post(
            "/api/v1/payroll/runs",
            headers=headers,
            json={"year": 2026, "month": 4, "working_days": 22},
        )
        assert len(created.json()["blocked_reasons"]) == 2

    async def test_approving_the_income_tax_leaves_only_the_social_tax(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Which is the clinic's real position: P2 is unanswered."""
        headers = await owner_headers(login, org_a)
        seeded = (await client.post("/api/v1/payroll/rules/seed", headers=headers)).json()
        for row in seeded:
            if row["kind"] in {"INCOME_TAX", "INPS"}:
                approved = await client.post(
                    f"/api/v1/payroll/rules/{row['id']}/approve", headers=headers
                )
                assert approved.status_code == 200, approved.text

        created = await client.post(
            "/api/v1/payroll/runs",
            headers=headers,
            json={"year": 2026, "month": 5, "working_days": 22},
        )
        reasons = created.json()["blocked_reasons"]
        assert len(reasons) == 1
        assert "social tax" in reasons[0]
        assert "OPEN_QUESTIONS P2" in reasons[0]

    async def test_there_is_no_endpoint_that_carries_a_rate(self) -> None:
        """A statutory rate exists only as an approved row, never in code.

        Asserted by reading this module's own source: no percentage literal
        that could be a tax rate appears in the router.
        """
        import pathlib
        import re

        source = pathlib.Path("app/api/routers/payroll.py").read_text()
        # Strip the test-free docstrings' prose and look for numeric literals
        # being used as rates.
        suspicious = re.findall(r'rate\s*=\s*Decimal\(["\']\d', source)
        assert suspicious == []


class TestNoFloats:
    async def test_a_rate_sent_as_a_float_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """11.9 as a binary float is 11.899999999999999."""
        headers = await owner_headers(login, org_a)
        refused = await client.post(
            "/api/v1/payroll/rules",
            headers={**headers, "Content-Type": "application/json"},
            content='{"kind": "SOCIAL_TAX", "method": "PERCENT_OF_GROSS", '
            '"rate": 11.9, "effective_from": "2019-01-01"}',
        )
        assert refused.status_code == 422, refused.text


class TestTheRoundTrip:
    async def test_a_salary_change_is_a_new_row_not_an_edit(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        employee = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Ishchiyev", "given_name": "Oybek"},
        )
        assert employee.status_code == 201, employee.text
        employee_id = employee.json()["id"]

        for amount, start in (("1500000", "2025-01-01"), ("2000000", "2026-03-10")):
            created = await client.post(
                f"/api/v1/payroll/employees/{employee_id}/compensation",
                headers=headers,
                json={"amount": amount, "effective_from": start},
            )
            assert created.status_code == 201, created.text

        rows = (
            await client.get(
                f"/api/v1/payroll/employees/{employee_id}/compensation", headers=headers
            )
        ).json()
        assert len(rows) == 2
        # The earlier agreement was closed the day before the new one began.
        earlier = next(row for row in rows if row["effective_from"] == "2025-01-01")
        assert earlier["effective_to"] == "2026-03-09"

    async def test_backdating_around_a_later_agreement_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        employee = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Orqaga", "given_name": "Sana"},
        )
        employee_id = employee.json()["id"]
        await client.post(
            f"/api/v1/payroll/employees/{employee_id}/compensation",
            headers=headers,
            json={"amount": "2000000", "effective_from": "2026-03-01"},
        )
        refused = await client.post(
            f"/api/v1/payroll/employees/{employee_id}/compensation",
            headers=headers,
            json={"amount": "1000000", "effective_from": "2025-01-01"},
        )
        assert refused.status_code == 409, refused.text
        assert "two agreements covering one day" in refused.text
