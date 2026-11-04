"""Employees, positions and the rota.

The rules worth a test are the ones that stop a payroll run guessing: an
employment is ENDED exactly when it has a leaving date, one login belongs to
one employee, and a record is never deleted — it is marked, with a reason.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.hr.enums import EmploymentStatus
from app.modules.hr.services.employees import ScheduleDayInput, display_name


async def a_position(employees, org_a, *, code="DENTIST", name="Dentist"):
    return await employees.create_position(organization_id=org_a.id, code=code, name=name)


async def an_employee(employees, org_a, *, family="Karimov", given="Aziz", **kwargs):
    return await employees.create(
        organization_id=org_a.id, family_name=family, given_name=given, **kwargs
    )


class TestPositions:
    async def test_a_position_is_created_with_an_upper_case_code(self, employees, org_a) -> None:
        position = await employees.create_position(
            organization_id=org_a.id, code=" nurse ", name=" Nurse "
        )
        assert position.code == "NURSE"
        assert position.name == "Nurse"
        assert position.is_active is True

    async def test_two_positions_cannot_share_a_code(self, employees, org_a) -> None:
        await a_position(employees, org_a)
        with pytest.raises(Conflict):
            await a_position(employees, org_a, name="Another dentist")

    async def test_a_blank_name_is_refused(self, employees, org_a) -> None:
        with pytest.raises(ValidationFailed):
            await employees.create_position(organization_id=org_a.id, code="X", name="  ")

    async def test_the_same_code_is_free_in_another_organization(
        self, employees, org_a, org_b
    ) -> None:
        await a_position(employees, org_a)
        other = await a_position(employees, org_b)
        assert other.code == "DENTIST"


class TestCreatingAnEmployee:
    async def test_it_gets_a_code_and_a_display_name(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a, patronymic="Akmalovich")
        assert employee.code.startswith("EMP-")
        assert display_name(employee) == "Karimov Aziz Akmalovich"

    async def test_somebody_starting_today_is_already_active(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a, hired_on=clinic_today())
        assert employee.status is EmploymentStatus.ACTIVE

    async def test_somebody_starting_next_month_is_prospective(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a, hired_on=clinic_today() + timedelta(days=30))
        assert employee.status is EmploymentStatus.PROSPECTIVE

    async def test_a_blank_name_is_refused(self, employees, org_a) -> None:
        with pytest.raises(ValidationFailed):
            await an_employee(employees, org_a, family="   ")

    async def test_creating_one_is_audited(self, employees, org_a, db: AsyncSession) -> None:
        employee = await an_employee(employees, org_a)
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.EMPLOYEE_CREATED,
                )
            )
        ).scalar_one()
        assert row.target_label == employee.code

    async def test_an_unknown_position_is_refused(self, employees, org_a) -> None:
        with pytest.raises(NotFound):
            await an_employee(employees, org_a, position_id=uuid.uuid4())

    async def test_another_organizations_position_is_refused(self, employees, org_a, org_b) -> None:
        theirs = await a_position(employees, org_b)
        with pytest.raises(NotFound):
            await an_employee(employees, org_a, position_id=theirs.id)


class TestLinkingALogin:
    async def test_a_member_may_be_linked(self, employees, org_a) -> None:
        cashier, _membership = org_a.members["cashier"]
        employee = await an_employee(employees, org_a, user_id=cashier.id)
        assert employee.user_id == cashier.id

    async def test_a_non_member_is_refused(self, employees, org_a, org_b) -> None:
        """An employee pointing at another tenant's user would name somebody
        else's staff on this organization's payroll."""
        with pytest.raises(ValidationFailed):
            await an_employee(employees, org_a, user_id=org_b.owner.id)

    async def test_one_login_belongs_to_one_employee(self, employees, org_a) -> None:
        cashier, _ = org_a.members["cashier"]
        await an_employee(employees, org_a, user_id=cashier.id)
        with pytest.raises(Conflict):
            await an_employee(
                employees, org_a, family="Imposterov", given="Boshqa", user_id=cashier.id
            )

    async def test_an_employee_may_have_no_login_at_all(self, employees, org_a) -> None:
        """Somebody can be paid without ever signing in."""
        employee = await an_employee(employees, org_a, family="Tozalovchi", given="Gul")
        assert employee.user_id is None

    async def test_linkable_users_leave_out_those_already_linked(self, employees, org_a) -> None:
        cashier, _ = org_a.members["cashier"]
        before = await employees.linkable_users(organization_id=org_a.id)
        assert cashier.id in {u.user_id for u in before}
        await an_employee(employees, org_a, user_id=cashier.id)
        after = await employees.linkable_users(organization_id=org_a.id)
        assert cashier.id not in {u.user_id for u in after}

    async def test_linkable_users_are_this_organizations_only(
        self, employees, org_a, org_b
    ) -> None:
        theirs = {u.user_id for u in await employees.linkable_users(organization_id=org_b.id)}
        ours = {u.user_id for u in await employees.linkable_users(organization_id=org_a.id)}
        assert theirs.isdisjoint(ours)


class TestEditing:
    async def test_a_detail_can_be_corrected(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        updated = await employees.update(
            organization_id=org_a.id,
            employee_id=employee.id,
            changes={"phone": "+998901234567"},
        )
        assert updated.phone == "+998901234567"

    async def test_the_status_cannot_be_changed_by_an_edit(self, employees, org_a) -> None:
        """Ending an employment must not look like correcting a phone number."""
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed) as excinfo:
            await employees.update(
                organization_id=org_a.id,
                employee_id=employee.id,
                changes={"status": "ENDED"},
            )
        assert "status" in excinfo.value.details["fields"]

    async def test_an_ended_record_is_not_edited(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=clinic_today(),
            reason="Moved away",
        )
        with pytest.raises(Conflict):
            await employees.update(
                organization_id=org_a.id, employee_id=employee.id, changes={"phone": "1"}
            )

    async def test_an_edit_that_changes_nothing_writes_no_audit_row(
        self, employees, org_a, db: AsyncSession
    ) -> None:
        employee = await an_employee(employees, org_a, phone="+998901112233")
        await db.flush()
        await employees.update(
            organization_id=org_a.id,
            employee_id=employee.id,
            changes={"phone": "+998901112233"},
        )
        await db.flush()
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_a.id,
                        AuditLog.action == events.EMPLOYEE_UPDATED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


class TestStatus:
    async def test_somebody_prospective_can_be_started(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a, hired_on=clinic_today() + timedelta(days=7))
        started = await employees.set_status(
            organization_id=org_a.id,
            employee_id=employee.id,
            status=EmploymentStatus.ACTIVE,
        )
        assert started.status is EmploymentStatus.ACTIVE

    async def test_somebody_active_can_be_suspended_and_brought_back(
        self, employees, org_a
    ) -> None:
        employee = await an_employee(employees, org_a)
        await employees.set_status(
            organization_id=org_a.id,
            employee_id=employee.id,
            status=EmploymentStatus.SUSPENDED,
        )
        back = await employees.set_status(
            organization_id=org_a.id,
            employee_id=employee.id,
            status=EmploymentStatus.ACTIVE,
        )
        assert back.status is EmploymentStatus.ACTIVE

    async def test_ending_cannot_go_through_the_status_endpoint(self, employees, org_a) -> None:
        """Leaving needs a date and a reason; this route has neither."""
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await employees.set_status(
                organization_id=org_a.id,
                employee_id=employee.id,
                status=EmploymentStatus.ENDED,
            )

    async def test_an_ended_employment_is_final(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=clinic_today(),
            reason="Retired",
        )
        with pytest.raises(Conflict):
            await employees.set_status(
                organization_id=org_a.id,
                employee_id=employee.id,
                status=EmploymentStatus.ACTIVE,
            )


class TestEndingEmployment:
    async def test_it_records_the_date_and_the_reason(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        when = clinic_today()
        ended = await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=when,
            reason="Moved to Samarkand",
        )
        assert ended.status is EmploymentStatus.ENDED
        assert ended.ended_on == when
        assert ended.ended_reason == "Moved to Samarkand"

    async def test_a_reason_is_required(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await employees.end_employment(
                organization_id=org_a.id,
                employee_id=employee.id,
                ended_on=clinic_today(),
                reason="   ",
            )

    async def test_nobody_leaves_before_they_started(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a, hired_on=date(2026, 6, 1))
        with pytest.raises(ValidationFailed):
            await employees.end_employment(
                organization_id=org_a.id,
                employee_id=employee.id,
                ended_on=date(2026, 5, 31),
                reason="Never started",
            )

    async def test_it_cannot_happen_twice(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=clinic_today(),
            reason="First",
        )
        with pytest.raises(Conflict):
            await employees.end_employment(
                organization_id=org_a.id,
                employee_id=employee.id,
                ended_on=clinic_today(),
                reason="Second",
            )

    async def test_the_record_is_kept_not_deleted(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=clinic_today(),
            reason="Left",
        )
        still_there = await employees.get(organization_id=org_a.id, employee_id=employee.id)
        assert still_there.id == employee.id

    async def test_it_is_audited_with_the_reason(self, employees, org_a, db: AsyncSession) -> None:
        employee = await an_employee(employees, org_a)
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=clinic_today(),
            reason="Moved to Samarkand",
        )
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.EMPLOYMENT_ENDED,
                )
            )
        ).scalar_one()
        assert row.reason == "Moved to Samarkand"

    async def test_it_is_in_the_always_audited_set(self) -> None:
        from app.modules.audit.events import REQUIRED_AUDIT_ACTIONS

        assert events.EMPLOYMENT_ENDED in REQUIRED_AUDIT_ACTIONS


class TestTheRota:
    async def test_a_week_is_stored_in_weekday_order(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        days = await employees.set_schedule(
            organization_id=org_a.id,
            employee_id=employee.id,
            days=[
                ScheduleDayInput(weekday=4, hours=Decimal("6")),
                ScheduleDayInput(weekday=2, hours=Decimal("8")),
            ],
        )
        assert [d.weekday for d in days] == [2, 4]

    async def test_setting_it_again_replaces_rather_than_merges(self, employees, org_a) -> None:
        """A rota is read as a whole; a merge would leave a day nobody meant."""
        employee = await an_employee(employees, org_a)
        await employees.set_schedule(
            organization_id=org_a.id,
            employee_id=employee.id,
            days=[ScheduleDayInput(weekday=1, hours=Decimal("8"))],
        )
        days = await employees.set_schedule(
            organization_id=org_a.id,
            employee_id=employee.id,
            days=[ScheduleDayInput(weekday=3, hours=Decimal("4"))],
        )
        assert [d.weekday for d in days] == [3]

    async def test_an_empty_rota_is_valid(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.set_schedule(
            organization_id=org_a.id,
            employee_id=employee.id,
            days=[ScheduleDayInput(weekday=1, hours=Decimal("8"))],
        )
        assert (
            await employees.set_schedule(organization_id=org_a.id, employee_id=employee.id, days=[])
            == []
        )

    async def test_the_same_weekday_twice_is_refused(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await employees.set_schedule(
                organization_id=org_a.id,
                employee_id=employee.id,
                days=[
                    ScheduleDayInput(weekday=2, hours=Decimal("8")),
                    ScheduleDayInput(weekday=2, hours=Decimal("4")),
                ],
            )

    async def test_an_impossible_weekday_is_refused(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await employees.set_schedule(
                organization_id=org_a.id,
                employee_id=employee.id,
                days=[ScheduleDayInput(weekday=8, hours=Decimal("8"))],
            )

    async def test_more_than_a_day_of_hours_is_refused(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await employees.set_schedule(
                organization_id=org_a.id,
                employee_id=employee.id,
                days=[ScheduleDayInput(weekday=1, hours=Decimal("25"))],
            )

    async def test_an_ended_employment_keeps_the_rota_it_had(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.set_schedule(
            organization_id=org_a.id,
            employee_id=employee.id,
            days=[ScheduleDayInput(weekday=1, hours=Decimal("8"))],
        )
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=clinic_today(),
            reason="Left",
        )
        with pytest.raises(Conflict):
            await employees.set_schedule(organization_id=org_a.id, employee_id=employee.id, days=[])

    async def test_scheduled_hours_answers_for_one_day(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        # 2026-11-03 is a Tuesday — ISO weekday 2.
        await employees.set_schedule(
            organization_id=org_a.id,
            employee_id=employee.id,
            days=[ScheduleDayInput(weekday=2, hours=Decimal("7.5"))],
        )
        assert await employees.scheduled_hours(
            organization_id=org_a.id, employee_id=employee.id, on=date(2026, 11, 3)
        ) == Decimal("7.5")
        assert (
            await employees.scheduled_hours(
                organization_id=org_a.id, employee_id=employee.id, on=date(2026, 11, 4)
            )
            is None
        )


class TestSearchAndIsolation:
    async def test_the_search_matches_a_name_and_a_code(self, employees, org_a) -> None:
        employee = await an_employee(employees, org_a, family="Yo'ldoshev", given="Olim")
        await an_employee(employees, org_a, family="Boshqa", given="Odam")
        by_name = await employees.list_employees(organization_id=org_a.id, query="yo'ldo")
        assert [e.id for e in by_name] == [employee.id]
        by_code = await employees.list_employees(organization_id=org_a.id, query=employee.code)
        assert [e.id for e in by_code] == [employee.id]

    async def test_the_search_does_not_match_a_telephone_number(self, employees, org_a) -> None:
        """A digit string typed into a name box must not find somebody by
        their number — the defect the patient search already fixed once."""
        await an_employee(employees, org_a, phone="+998901234567")
        assert await employees.list_employees(organization_id=org_a.id, query="9012345") == []

    async def test_the_list_filters_by_status(self, employees, org_a) -> None:
        active = await an_employee(employees, org_a)
        leaver = await an_employee(employees, org_a, family="Ketganov", given="Bek")
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=leaver.id,
            ended_on=clinic_today(),
            reason="Left",
        )
        rows = await employees.list_employees(
            organization_id=org_a.id, statuses=[EmploymentStatus.ACTIVE]
        )
        assert [e.id for e in rows] == [active.id]

    async def test_an_employee_is_invisible_from_another_organization(
        self, employees, org_a, org_b
    ) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(NotFound):
            await employees.get(organization_id=org_b.id, employee_id=employee.id)

    async def test_another_organization_cannot_end_an_employment(
        self, employees, org_a, org_b
    ) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(NotFound):
            await employees.end_employment(
                organization_id=org_b.id,
                employee_id=employee.id,
                ended_on=clinic_today(),
                reason="Not theirs to end",
            )
