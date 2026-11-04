"""The reports over HTTP: who may run them, and what they say when blocked.

Two things are checked here that the service tests cannot. The first is the
permission boundary across *every* route in the module, swept rather than
spot-checked: a reader who may see a figure may also see the entries behind
it, and that only holds if nothing in the module is left unguarded.

The second is the catalogue — the endpoint whose whole job is to tell the
truth about what this build can produce, including which statutory forms are
blocked and by which open question. It exists so the screen never has to
guess, and so a module-not-developed message can never appear over a report
that works.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"

#: Every read endpoint in the module, with the arguments it needs.
REPORTS = [
    "/api/v1/reporting/catalogue",
    "/api/v1/reporting/profit-and-loss?from_date=2026-01-01&to_date=2026-12-31",
    "/api/v1/reporting/balance-sheet?as_of=2026-12-31",
    "/api/v1/reporting/receivables-ageing",
    "/api/v1/reporting/payables-ageing",
    "/api/v1/reporting/cash-flow?from_date=2026-01-01&to_date=2026-12-31",
]

EXPORTS = [
    "/api/v1/reporting/profit-and-loss/export?from_date=2026-01-01&to_date=2026-12-31",
    "/api/v1/reporting/balance-sheet/export?as_of=2026-12-31",
    "/api/v1/reporting/receivables-ageing/export",
    "/api/v1/reporting/payables-ageing/export",
    "/api/v1/reporting/cash-flow/export?from_date=2026-01-01&to_date=2026-12-31",
]

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def owner_headers(login, org_a):
    return await login(f"owner@{org_a.organization.code}.example.com", OWNER_PASSWORD)


async def staff_headers(login, org_a, label):
    return await login(f"{label}@{org_a.organization.code}.example.com", STAFF_PASSWORD)


class TestWhoMayRunAReport:
    @pytest.mark.parametrize("path", REPORTS + EXPORTS)
    async def test_the_cashier_is_refused_every_report(
        self, client: AsyncClient, login, org_a, path: str
    ) -> None:
        """A cashier holds no `reporting.*` permission at all.

        Swept across every route rather than spot-checked: an unguarded
        endpoint in a module like this leaks the whole ledger in aggregate.
        """
        headers = await staff_headers(login, org_a, "cashier")
        response = await client.get(path, headers=headers)
        assert response.status_code == 403, f"{path} → {response.status_code}"
        assert "reporting.financial.read" in response.text

    @pytest.mark.parametrize("path", REPORTS)
    async def test_the_accountant_may_read_every_report(
        self, client: AsyncClient, login, org_a, path: str
    ) -> None:
        headers = await staff_headers(login, org_a, "accountant")
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, f"{path} → {response.text}"

    @pytest.mark.parametrize("path", REPORTS)
    async def test_the_auditor_may_read_every_report(
        self, client: AsyncClient, login, org_a, path: str
    ) -> None:
        """An auditor reads everything and changes nothing."""
        headers = await staff_headers(login, org_a, "auditor")
        assert (await client.get(path, headers=headers)).status_code == 200


class TestTheCatalogueTellsTheTruth:
    async def test_it_reports_what_this_build_can_produce(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        body = (await client.get("/api/v1/reporting/catalogue", headers=headers)).json()
        available = {row["key"] for row in body["reports"] if row["available"]}
        assert {
            "profit_and_loss",
            "balance_sheet",
            "receivables_ageing",
            "payables_ageing",
            "cash_flow",
            "trial_balance",
            "account_statement",
        } <= available

    async def test_statutory_forms_are_blocked_and_name_their_question(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Blocked, and said so by the backend — not hidden by the screen."""
        headers = await owner_headers(login, org_a)
        body = (await client.get("/api/v1/reporting/catalogue", headers=headers)).json()
        blocked = {row["key"]: row for row in body["reports"] if not row["available"]}
        assert blocked["statutory_financial_statements"]["blocked_by"] == "A2"
        assert blocked["statutory_cash_flow"]["blocked_by"] == "A3"
        assert blocked["vat_return"]["blocked_by"] == "A5"
        assert all(row["statutory"] for row in blocked.values())

    async def test_there_is_no_endpoint_that_produces_a_statutory_form(self) -> None:
        """The absence is the control, so it is asserted rather than assumed."""
        from app.main import app

        # Scoped to this module: seeding the statutory *chart* is a real
        # endpoint elsewhere and has nothing to do with filing a form.
        reporting_paths = [p for p in app.openapi()["paths"] if "/reporting/" in p]
        assert reporting_paths
        for path in reporting_paths:
            assert "statutory" not in path
            assert "vat" not in path


class TestEmptyIsAnAnswer:
    async def test_a_clinic_with_no_entries_reports_zero_rather_than_failing(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        profit = await client.get(
            "/api/v1/reporting/profit-and-loss?from_date=2026-01-01&to_date=2026-12-31",
            headers=headers,
        )
        assert profit.status_code == 200
        assert profit.json()["net_result"] == "0.00"
        assert profit.json()["revenue"]["lines"] == []

    async def test_payables_say_why_they_are_empty(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A clinic with no suppliers sees that stated, not a blank table."""
        headers = await owner_headers(login, org_a)
        body = (await client.get("/api/v1/reporting/payables-ageing", headers=headers)).json()
        assert body["no_suppliers_yet"] is True
        assert body["rows"] == []

    async def test_cash_flow_says_when_no_till_is_configured(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        body = (
            await client.get(
                "/api/v1/reporting/cash-flow?from_date=2026-01-01&to_date=2026-12-31",
                headers=headers,
            )
        ).json()
        assert body["no_cash_accounts_configured"] is True
        assert body["reconciles"] is True


class TestWindowsAreChecked:
    async def test_a_period_that_ends_before_it_starts_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        response = await client.get(
            "/api/v1/reporting/profit-and-loss?from_date=2026-12-31&to_date=2026-01-01",
            headers=headers,
        )
        assert response.status_code == 422, response.text
        assert "before its start" in response.text

    async def test_half_a_comparative_period_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Guessing which comparison somebody meant is how a report answers
        a question nobody asked."""
        headers = await owner_headers(login, org_a)
        response = await client.get(
            "/api/v1/reporting/profit-and-loss"
            "?from_date=2026-01-01&to_date=2026-12-31&compare_from=2025-01-01",
            headers=headers,
        )
        assert response.status_code == 422, response.text
        assert "both a start and an end" in response.text

    async def test_a_malformed_date_is_refused_by_the_schema(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        response = await client.get(
            "/api/v1/reporting/balance-sheet?as_of=not-a-date", headers=headers
        )
        assert response.status_code == 422


class TestExports:
    @pytest.mark.parametrize("path", EXPORTS)
    async def test_every_report_downloads_as_a_spreadsheet(
        self, client: AsyncClient, login, org_a, path: str
    ) -> None:
        headers = await owner_headers(login, org_a)
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, f"{path} → {response.text[:200]}"
        assert response.headers["content-type"] == XLSX
        assert "attachment; filename=" in response.headers["content-disposition"]
        # A real workbook, not an empty body: every .xlsx is a zip archive.
        assert response.content[:2] == b"PK"
        assert len(response.content) > 1000

    async def test_the_spreadsheet_carries_the_figures_it_was_asked_for(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """The export is the report, not a second computation."""
        import io

        from openpyxl import load_workbook

        headers = await owner_headers(login, org_a)
        response = await client.get(
            "/api/v1/reporting/balance-sheet/export?as_of=2026-12-31", headers=headers
        )
        workbook = load_workbook(io.BytesIO(response.content))
        text = "\n".join(
            str(cell.value)
            for row in workbook.active.iter_rows()
            for cell in row
            if cell.value is not None
        )
        assert "Balance sheet (management)" in text
        assert "2026-12-31" in text
        # The statement says it is not a filing form, in the file itself.
        assert "statutory" in text.lower()
