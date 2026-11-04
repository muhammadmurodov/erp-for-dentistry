"""The employee module over HTTP, as the people who would really use it.

The service tests cover the rules. These cover the boundary: who may reach
each endpoint, what a refusal says, and whether a round trip through the API
produces the same record the service would.
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
    async def test_the_owner_may_do_everything(self, client: AsyncClient, login, org_a) -> None:
        headers = await owner_headers(login, org_a)
        assert (await client.get("/api/v1/hr/employees", headers=headers)).status_code == 200
        created = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Egasi", "given_name": "Bosh"},
        )
        assert created.status_code == 201, created.text

    async def test_the_cashier_may_not_see_the_staff_list(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A cashier holds no `hr.*` permission at all."""
        headers = await staff_headers(login, org_a, "cashier")
        assert (await client.get("/api/v1/hr/employees", headers=headers)).status_code == 403

    async def test_the_receptionist_may_not_see_the_staff_list(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "receptionist")
        assert (await client.get("/api/v1/hr/employees", headers=headers)).status_code == 403

    async def test_the_auditor_reads_but_cannot_hire(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "auditor")
        assert (await client.get("/api/v1/hr/employees", headers=headers)).status_code == 200
        refused = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Auditorov", "given_name": "Yangi"},
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "permission_denied"

    async def test_the_accountant_reads_but_cannot_hire(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """`accountant` holds `hr.employee.read` and nothing more."""
        headers = await staff_headers(login, org_a, "accountant")
        assert (await client.get("/api/v1/hr/employees", headers=headers)).status_code == 200
        refused = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Hisobchiyev", "given_name": "Yangi"},
        )
        assert refused.status_code == 403

    async def test_the_accountant_cannot_record_attendance(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Recording a day is its own permission, not part of reading staff."""
        headers = await staff_headers(login, org_a, "accountant")
        refused = await client.post(
            "/api/v1/hr/attendance",
            headers=headers,
            json={
                "employee_id": "00000000-0000-0000-0000-000000000000",
                "on_date": "2026-11-03",
                "code": "SICK",
            },
        )
        assert refused.status_code == 403

    async def test_an_unauthenticated_caller_gets_nowhere(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/hr/employees")).status_code == 401


class TestARoundTrip:
    async def test_hire_record_attend_and_leave(self, client: AsyncClient, login, org_a) -> None:
        headers = await owner_headers(login, org_a)

        position = await client.post(
            "/api/v1/hr/positions",
            headers=headers,
            json={"code": "nurse", "name": "Nurse"},
        )
        assert position.status_code == 201, position.text
        assert position.json()["code"] == "NURSE"

        hired = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={
                "family_name": "Hamroyeva",
                "given_name": "Nilufar",
                "position_id": position.json()["id"],
                "hired_on": "2026-01-15",
                "phone": "+998901234567",
            },
        )
        assert hired.status_code == 201, hired.text
        employee = hired.json()
        assert employee["status"] == "ACTIVE"
        assert employee["position_name"] == "Nurse"
        assert employee["display_name"] == "Hamroyeva Nilufar"

        rota = await client.put(
            f"/api/v1/hr/employees/{employee['id']}/schedule",
            headers=headers,
            json={"days": [{"weekday": 2, "hours": "7.5"}, {"weekday": 4, "hours": "7.5"}]},
        )
        assert rota.status_code == 200, rota.text
        assert [d["weekday"] for d in rota.json()] == [2, 4]

        marked = await client.post(
            "/api/v1/hr/attendance",
            headers=headers,
            json={
                "employee_id": employee["id"],
                "on_date": "2026-02-03",
                "code": "WORKED",
                "hours": "7.5",
            },
        )
        assert marked.status_code == 200, marked.text

        summary = await client.get(
            "/api/v1/hr/attendance/summary",
            headers=headers,
            params={
                "employee_id": employee["id"],
                "from_date": "2026-02-01",
                "to_date": "2026-02-28",
            },
        )
        assert summary.status_code == 200
        assert summary.json()["days_present"] == 1
        assert summary.json()["hours"] == "7.50"

        left = await client.post(
            f"/api/v1/hr/employees/{employee['id']}/end",
            headers=headers,
            json={"ended_on": "2026-06-30", "reason": "Moved to Samarkand"},
        )
        assert left.status_code == 200, left.text
        assert left.json()["status"] == "ENDED"
        assert left.json()["ended_on"] == "2026-06-30"

        # The record is kept, not deleted.
        again = await client.get(f"/api/v1/hr/employees/{employee['id']}", headers=headers)
        assert again.status_code == 200
        assert again.json()["ended_reason"] == "Moved to Samarkand"


class TestWhatTheApiRefuses:
    async def test_a_float_for_hours_is_refused(self, client: AsyncClient, login, org_a) -> None:
        """7.699999999999999 on a payslip is not an edge case, it is a bug."""
        headers = await owner_headers(login, org_a)
        hired = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Floatov", "given_name": "Son", "hired_on": "2026-01-01"},
        )
        refused = await client.post(
            "/api/v1/hr/attendance",
            headers=headers,
            json={
                "employee_id": hired.json()["id"],
                "on_date": "2026-02-02",
                "code": "WORKED",
                "hours": 7.7,
            },
        )
        assert refused.status_code == 422

    async def test_ending_without_a_reason_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        hired = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Sababsiz", "given_name": "Ketish"},
        )
        refused = await client.post(
            f"/api/v1/hr/employees/{hired.json()['id']}/end",
            headers=headers,
            json={"ended_on": "2026-12-31", "reason": ""},
        )
        assert refused.status_code == 422

    async def test_an_unknown_field_on_an_edit_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """The status must not be reachable through a general-purpose edit."""
        headers = await owner_headers(login, org_a)
        hired = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Tahrirov", "given_name": "Xato"},
        )
        refused = await client.patch(
            f"/api/v1/hr/employees/{hired.json()['id']}",
            headers=headers,
            json={"status": "ENDED"},
        )
        assert refused.status_code == 422

    async def test_another_tenants_employee_is_not_found(
        self, client: AsyncClient, login, org_a, org_b
    ) -> None:
        theirs = await login(f"owner@{org_b.organization.code}.example.com", OWNER_PASSWORD)
        mine = await owner_headers(login, org_a)
        hired = await client.post(
            "/api/v1/hr/employees",
            headers=mine,
            json={"family_name": "Mening", "given_name": "Xodimim"},
        )
        probed = await client.get(f"/api/v1/hr/employees/{hired.json()['id']}", headers=theirs)
        assert probed.status_code == 404


class TestRolesComeFromTheMembership:
    async def test_a_linked_employee_shows_the_roles_their_login_holds(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """One person may hold several roles; this module never stores them."""
        headers = await owner_headers(login, org_a)
        cashier, _membership = org_a.members["cashier"]
        hired = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={
                "family_name": "Kassirov",
                "given_name": "Oila",
                "user_id": str(cashier.id),
            },
        )
        assert hired.status_code == 201, hired.text
        assert hired.json()["role_codes"] == ["cashier"]

    async def test_an_employee_with_no_login_holds_no_roles(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        hired = await client.post(
            "/api/v1/hr/employees",
            headers=headers,
            json={"family_name": "Tozalovchi", "given_name": "Gulnora"},
        )
        assert hired.json()["role_codes"] == []

    async def test_linkable_users_is_behind_the_manage_permission(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """It lists who may be given an employee record — not a read-only fact."""
        reader = await staff_headers(login, org_a, "accountant")
        assert (
            await client.get("/api/v1/hr/employees/linkable-users", headers=reader)
        ).status_code == 403
