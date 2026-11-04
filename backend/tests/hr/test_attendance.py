"""Attendance: what each person's each day actually was.

Payroll will read these rows, so the tests here are mostly about what cannot
be recorded: a day twice, hours on a sick day, a day before somebody started,
or a future day marked as worked.
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
from app.modules.hr.enums import AttendanceCode

YESTERDAY = clinic_today() - timedelta(days=1)
TODAY = clinic_today()
TOMORROW = clinic_today() + timedelta(days=1)


async def an_employee(employees, org_a, *, family="Davomov", given="Zafar", **kwargs):
    kwargs.setdefault("hired_on", clinic_today() - timedelta(days=365))
    return await employees.create(
        organization_id=org_a.id, family_name=family, given_name=given, **kwargs
    )


class TestRecordingADay:
    async def test_a_worked_day_carries_its_hours(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        day = await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.WORKED,
            hours=Decimal("7.5"),
        )
        assert day.code is AttendanceCode.WORKED
        assert day.hours == Decimal("7.50")

    async def test_a_worked_day_without_hours_is_refused(
        self, attendance, employees, org_a
    ) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=TODAY,
                code=AttendanceCode.WORKED,
            )

    async def test_a_sick_day_with_hours_is_refused(self, attendance, employees, org_a) -> None:
        """Six hours on a day marked sick is a contradiction, not an edge case."""
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=TODAY,
                code=AttendanceCode.SICK,
                hours=Decimal("6"),
            )

    async def test_more_than_a_day_of_hours_is_refused(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=TODAY,
                code=AttendanceCode.WORKED,
                hours=Decimal("25"),
            )

    async def test_recording_is_audited(
        self, attendance, employees, org_a, db: AsyncSession
    ) -> None:
        employee = await an_employee(employees, org_a)
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.SICK,
        )
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.ATTENDANCE_RECORDED,
                )
            )
        ).scalar_one()
        assert row.new_values["code"] == "SICK"


class TestCorrectingADay:
    async def test_recording_the_same_day_twice_overwrites(
        self, attendance, employees, org_a
    ) -> None:
        """A day has one truth, and a correction is the ordinary case."""
        employee = await an_employee(employees, org_a)
        first = await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.ABSENT,
        )
        second = await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.WORKED,
            hours=Decimal("8"),
        )
        assert second.id == first.id
        assert second.code is AttendanceCode.WORKED

        days = await attendance.list_days(organization_id=org_a.id, employee_id=employee.id)
        assert len(days) == 1

    async def test_the_correction_keeps_what_it_replaced(
        self, attendance, employees, org_a, db: AsyncSession
    ) -> None:
        employee = await an_employee(employees, org_a)
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.ABSENT,
        )
        await db.flush()
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.SICK,
        )
        await db.flush()
        # The correction is the row that carries a previous value; the two
        # rows share a timestamp, so they are told apart by what they say,
        # not by the order they come back in.
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_a.id,
                        AuditLog.action == events.ATTENDANCE_RECORDED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        correction = next(r for r in rows if r.old_values)
        assert correction.old_values["code"] == "ABSENT"
        assert correction.new_values["code"] == "SICK"

    async def test_a_day_can_be_removed_and_the_removal_is_recorded(
        self, attendance, employees, org_a, db: AsyncSession
    ) -> None:
        employee = await an_employee(employees, org_a)
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.LEAVE,
        )
        await attendance.delete_day(organization_id=org_a.id, employee_id=employee.id, on=TODAY)
        assert (
            await attendance.get_day(organization_id=org_a.id, employee_id=employee.id, on=TODAY)
            is None
        )
        await db.flush()
        removal = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.ATTENDANCE_REMOVED,
                )
            )
        ).scalar_one()
        assert removal.old_values["code"] == "LEAVE"

    async def test_removing_a_day_that_was_never_recorded_is_not_found(
        self, attendance, employees, org_a
    ) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(NotFound):
            await attendance.delete_day(organization_id=org_a.id, employee_id=employee.id, on=TODAY)


class TestWhoAndWhen:
    async def test_a_day_before_somebody_started_is_refused(
        self, attendance, employees, org_a
    ) -> None:
        employee = await an_employee(employees, org_a, hired_on=TODAY)
        with pytest.raises(ValidationFailed):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=YESTERDAY,
                code=AttendanceCode.WORKED,
                hours=Decimal("8"),
            )

    async def test_a_day_after_somebody_left_is_refused(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        await employees.end_employment(
            organization_id=org_a.id,
            employee_id=employee.id,
            ended_on=YESTERDAY,
            reason="Left",
        )
        with pytest.raises(ValidationFailed):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=TODAY,
                code=AttendanceCode.WORKED,
                hours=Decimal("8"),
            )

    async def test_somebody_who_has_not_started_cannot_be_marked_present(
        self, attendance, employees, org_a
    ) -> None:
        employee = await an_employee(employees, org_a, hired_on=TODAY + timedelta(days=10))
        with pytest.raises(Conflict):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=TODAY,
                code=AttendanceCode.WORKED,
                hours=Decimal("8"),
            )

    async def test_a_future_day_cannot_be_marked_worked(self, attendance, employees, org_a) -> None:
        """Marking somebody present next week is a rota, and one exists."""
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=TOMORROW,
                code=AttendanceCode.WORKED,
                hours=Decimal("8"),
            )

    async def test_future_leave_may_be_booked(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        day = await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TOMORROW,
            code=AttendanceCode.LEAVE,
        )
        assert day.code is AttendanceCode.LEAVE

    async def test_an_unknown_employee_is_not_found(self, attendance, org_a) -> None:
        with pytest.raises(NotFound):
            await attendance.record(
                organization_id=org_a.id,
                employee_id=uuid.uuid4(),
                on=TODAY,
                code=AttendanceCode.SICK,
            )


class TestSummarising:
    async def test_it_counts_each_kind_of_day_and_totals_the_hours(
        self, attendance, employees, org_a
    ) -> None:
        employee = await an_employee(employees, org_a)
        start = TODAY - timedelta(days=4)
        plan = [
            (0, AttendanceCode.WORKED, Decimal("8")),
            (1, AttendanceCode.WORKED, Decimal("7.5")),
            (2, AttendanceCode.SICK, None),
            (3, AttendanceCode.LEAVE, None),
            (4, AttendanceCode.ABSENT, None),
        ]
        for offset, code, hours in plan:
            await attendance.record(
                organization_id=org_a.id,
                employee_id=employee.id,
                on=start + timedelta(days=offset),
                code=code,
                hours=hours,
            )
        summary = await attendance.summarise(
            organization_id=org_a.id,
            employee_id=employee.id,
            from_date=start,
            to_date=TODAY,
        )
        assert summary.days_present == 2
        assert summary.days_sick == 1
        assert summary.days_leave == 1
        assert summary.days_absent == 1
        assert summary.hours == Decimal("15.50")

    async def test_a_holiday_is_not_time_at_work(self, attendance, employees, org_a) -> None:
        """The clinic was closed. Counting it as present would pay for it."""
        employee = await an_employee(employees, org_a)
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.HOLIDAY,
        )
        summary = await attendance.summarise(
            organization_id=org_a.id,
            employee_id=employee.id,
            from_date=TODAY,
            to_date=TODAY,
        )
        assert summary.days_present == 0
        assert summary.days_holiday == 1

    async def test_an_empty_period_totals_nothing(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        summary = await attendance.summarise(
            organization_id=org_a.id,
            employee_id=employee.id,
            from_date=TODAY - timedelta(days=30),
            to_date=TODAY - timedelta(days=25),
        )
        assert summary.days_present == 0
        assert summary.hours == Decimal("0")

    async def test_a_backwards_period_is_refused(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(ValidationFailed):
            await attendance.summarise(
                organization_id=org_a.id,
                employee_id=employee.id,
                from_date=TODAY,
                to_date=TODAY - timedelta(days=1),
            )

    async def test_it_counts_only_the_person_asked_about(
        self, attendance, employees, org_a
    ) -> None:
        one = await an_employee(employees, org_a)
        two = await an_employee(employees, org_a, family="Ikkinchi", given="Xodim")
        await attendance.record(
            organization_id=org_a.id,
            employee_id=one.id,
            on=TODAY,
            code=AttendanceCode.WORKED,
            hours=Decimal("8"),
        )
        await attendance.record(
            organization_id=org_a.id,
            employee_id=two.id,
            on=TODAY,
            code=AttendanceCode.WORKED,
            hours=Decimal("4"),
        )
        summary = await attendance.summarise(
            organization_id=org_a.id, employee_id=one.id, from_date=TODAY, to_date=TODAY
        )
        assert summary.hours == Decimal("8.00")


class TestTheMonthGrid:
    async def test_it_keys_a_month_by_person_and_day(self, attendance, employees, org_a) -> None:
        employee = await an_employee(employees, org_a)
        on = date(TODAY.year, TODAY.month, 1)
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=on,
            code=AttendanceCode.WORKED,
            hours=Decimal("8"),
        )
        grid = await attendance.month_grid(organization_id=org_a.id, year=on.year, month=on.month)
        assert grid[employee.id][on].hours == Decimal("8.00")

    async def test_an_impossible_month_is_refused(self, attendance, org_a) -> None:
        with pytest.raises(ValidationFailed):
            await attendance.month_grid(organization_id=org_a.id, year=2026, month=13)

    async def test_december_does_not_run_into_next_year(self, attendance, employees, org_a) -> None:
        """The month's last day is computed, and December is where that breaks."""
        employee = await an_employee(employees, org_a, hired_on=date(2025, 1, 1))
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=date(2025, 12, 31),
            code=AttendanceCode.WORKED,
            hours=Decimal("6"),
        )
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=date(2026, 1, 1),
            code=AttendanceCode.WORKED,
            hours=Decimal("6"),
        )
        grid = await attendance.month_grid(organization_id=org_a.id, year=2025, month=12)
        assert date(2025, 12, 31) in grid[employee.id]
        assert date(2026, 1, 1) not in grid[employee.id]


class TestIsolation:
    async def test_attendance_is_invisible_from_another_organization(
        self, attendance, employees, org_a, org_b
    ) -> None:
        employee = await an_employee(employees, org_a)
        await attendance.record(
            organization_id=org_a.id,
            employee_id=employee.id,
            on=TODAY,
            code=AttendanceCode.WORKED,
            hours=Decimal("8"),
        )
        assert await attendance.list_days(organization_id=org_b.id) == []

    async def test_another_organization_cannot_record_against_an_employee(
        self, attendance, employees, org_a, org_b
    ) -> None:
        employee = await an_employee(employees, org_a)
        with pytest.raises(NotFound):
            await attendance.record(
                organization_id=org_b.id,
                employee_id=employee.id,
                on=TODAY,
                code=AttendanceCode.SICK,
            )
