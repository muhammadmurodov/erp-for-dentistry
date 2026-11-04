"""Attendance: what each person's each day actually was.

Attendance is evidence, not a plan. The rota says what a normal Tuesday
looks like; this says what happened. Payroll reads this one, never the rota,
which is why the two are separate tables and why recording a day is its own
permission (`hr.attendance.record`) rather than part of editing an employee.

Two invariants are held by the database as well as here: one row per person
per day, and hours only on a day that was worked. Both are the kind of thing
that, allowed through once, becomes an overpayment nobody can explain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.hr.enums import (
    CODES_WITH_HOURS,
    PRESENT_CODES,
    AttendanceCode,
    EmploymentStatus,
)
from app.modules.hr.models import AttendanceDay
from app.modules.hr.services.employees import EmployeeService

#: How far ahead a day may be recorded. Marking somebody present next week is
#: not attendance, it is a rota — and a rota already exists. Agreed leave is
#: the one thing people genuinely book ahead, so it is allowed to.
FUTURE_CODES = frozenset({AttendanceCode.LEAVE, AttendanceCode.HOLIDAY})


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    """What a stretch of days came to, for one person.

    `days_present` and `hours` are what payroll would read; the rest is what
    a person looking at the month wants to see. Nothing here is money.
    """

    employee_id: uuid.UUID
    from_date: date
    to_date: date
    days_present: int
    days_absent: int
    days_sick: int
    days_leave: int
    days_holiday: int
    hours: Decimal


class AttendanceService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._employees = EmployeeService(session, audit)

    async def list_days(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[AttendanceDay]:
        stmt = select(AttendanceDay).where(AttendanceDay.organization_id == organization_id)
        if employee_id is not None:
            stmt = stmt.where(AttendanceDay.employee_id == employee_id)
        if from_date is not None:
            stmt = stmt.where(AttendanceDay.on_date >= from_date)
        if to_date is not None:
            stmt = stmt.where(AttendanceDay.on_date <= to_date)
        stmt = stmt.order_by(AttendanceDay.on_date.desc(), AttendanceDay.employee_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_day(
        self, *, organization_id: uuid.UUID, employee_id: uuid.UUID, on: date
    ) -> AttendanceDay | None:
        stmt = select(AttendanceDay).where(
            AttendanceDay.organization_id == organization_id,
            AttendanceDay.employee_id == employee_id,
            AttendanceDay.on_date == on,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def record(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        on: date,
        code: AttendanceCode,
        hours: Decimal | None = None,
        note: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> AttendanceDay:
        """Record, or correct, one person's one day.

        Recording the same day twice **overwrites** rather than refusing or
        adding. The day has one truth and a correction is the ordinary case —
        somebody marked absent who turns out to have been at the dentist's
        own dentist. The old value goes into the audit record, so the
        correction is visible rather than silent.
        """
        employee = await self._employees.get(
            organization_id=organization_id, employee_id=employee_id
        )
        if employee.status is EmploymentStatus.PROSPECTIVE:
            raise Conflict(
                f"{employee.code} has not started yet",
                details={"hired_on": str(employee.hired_on)},
            )
        if on < employee.hired_on:
            raise ValidationFailed(
                "That day is before this person started",
                details={"hired_on": str(employee.hired_on)},
            )
        if employee.ended_on is not None and on > employee.ended_on:
            raise ValidationFailed(
                "That day is after this person left",
                details={"ended_on": str(employee.ended_on)},
            )

        today = clinic_today()
        if on > today and code not in FUTURE_CODES:
            raise ValidationFailed(
                "A day in the future cannot be marked as worked, absent or sick. "
                "Agreed leave and a holiday can be.",
                details={"code": code.value, "on": str(on)},
            )

        if code in CODES_WITH_HOURS:
            if hours is None:
                raise ValidationFailed("A day that was worked needs its hours")
            if hours <= 0 or hours > 24:
                raise ValidationFailed(
                    "Hours worked must be above nothing and at most 24",
                    details={"hours": str(hours)},
                )
        elif hours is not None:
            raise ValidationFailed(
                f"A day marked {code.value.lower()} carries no hours",
                details={"code": code.value},
            )

        existing = await self.get_day(
            organization_id=organization_id, employee_id=employee_id, on=on
        )
        if existing is not None:
            old = {"code": existing.code.value, "hours": existing.hours}
            existing.code = code
            existing.hours = hours
            existing.note = note
            existing.recorded_by_user_id = actor_user_id
            await self._session.flush()
            self._audit.record(
                events.ATTENDANCE_RECORDED,
                organization_id=organization_id,
                target_type="attendance_day",
                target_id=existing.id,
                target_label=f"{employee.code} {on}",
                business_event=f"attendance:{employee_id}:{on}",
                actor_user_id=actor_user_id,
                old_values=old,
                new_values={"code": code.value, "hours": hours},
            )
            return existing

        day = AttendanceDay(
            organization_id=organization_id,
            employee_id=employee_id,
            on_date=on,
            code=code,
            hours=hours,
            note=note,
            recorded_by_user_id=actor_user_id,
        )
        self._session.add(day)
        await self._session.flush()

        self._audit.record(
            events.ATTENDANCE_RECORDED,
            organization_id=organization_id,
            target_type="attendance_day",
            target_id=day.id,
            target_label=f"{employee.code} {on}",
            business_event=f"attendance:{employee_id}:{on}",
            actor_user_id=actor_user_id,
            new_values={"code": code.value, "hours": hours},
        )
        return day

    async def delete_day(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        on: date,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        """Remove a day recorded by mistake.

        Allowed, unlike almost everything else in this system, because an
        attendance row is not a financial posting: nothing has been paid on
        the strength of it yet. Once payroll reads a period, that stops being
        true — and the payroll module is what will lock the period, because
        it is the one that knows a run has happened.
        """
        day = await self.get_day(organization_id=organization_id, employee_id=employee_id, on=on)
        if day is None:
            raise NotFound("No attendance recorded for that day")
        employee = await self._employees.get(
            organization_id=organization_id, employee_id=employee_id
        )
        old = {"code": day.code.value, "hours": day.hours}
        await self._session.delete(day)
        await self._session.flush()
        self._audit.record(
            events.ATTENDANCE_REMOVED,
            organization_id=organization_id,
            target_type="attendance_day",
            target_id=day.id,
            target_label=f"{employee.code} {on}",
            business_event=f"attendance:{employee_id}:{on}",
            actor_user_id=actor_user_id,
            old_values=old,
        )

    async def summarise(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        from_date: date,
        to_date: date,
    ) -> AttendanceSummary:
        """Total a stretch of days for one person.

        Derived on every call rather than stored. A stored total is a second
        answer that drifts from the rows it came from, and the rows are what
        anybody would check it against.
        """
        if to_date < from_date:
            raise ValidationFailed("The end of the period is before its start")
        await self._employees.get(organization_id=organization_id, employee_id=employee_id)

        days = await self.list_days(
            organization_id=organization_id,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
        )
        counts = dict.fromkeys(AttendanceCode, 0)
        hours = Decimal("0")
        for day in days:
            counts[day.code] += 1
            if day.hours is not None:
                hours += day.hours

        return AttendanceSummary(
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            days_present=sum(counts[c] for c in PRESENT_CODES),
            days_absent=counts[AttendanceCode.ABSENT],
            days_sick=counts[AttendanceCode.SICK],
            days_leave=counts[AttendanceCode.LEAVE],
            days_holiday=counts[AttendanceCode.HOLIDAY],
            hours=hours,
        )

    async def month_grid(
        self, *, organization_id: uuid.UUID, year: int, month: int
    ) -> dict[uuid.UUID, dict[date, AttendanceDay]]:
        """Every employee's month, keyed for a timesheet screen.

        One query rather than one per person: a clinic of three makes that
        immaterial, but the shape is what a larger one would need and the
        cost of writing it this way now is nothing.
        """
        if month < 1 or month > 12:
            raise ValidationFailed("Month runs from 1 to 12", details={"month": month})
        first = date(year, month, 1)
        last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        days = await self.list_days(organization_id=organization_id, from_date=first, to_date=last)
        grid: dict[uuid.UUID, dict[date, AttendanceDay]] = {}
        for day in days:
            grid.setdefault(day.employee_id, {})[day.on_date] = day
        return grid
