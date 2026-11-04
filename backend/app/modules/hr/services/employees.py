"""Employees, positions and the rota.

What this service refuses to do is as much the point as what it does.

It does not create logins. Hiring somebody and giving them access to the
books are two decisions with two different permissions, and a service that
did both would let `hr.employee.manage` mint an account with roles on it.
Linking an employee to an existing user is allowed; creating the user is the
identity module's job, behind `identity.user.manage`.

It stores no compensation. A rate on an employee row would be a second
answer to what somebody is paid, and the first answer belongs to payroll.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder, changed_fields
from app.modules.hr.enums import EmploymentStatus
from app.modules.hr.models import AttendanceDay, Employee, JobPosition, WorkScheduleDay
from app.modules.identity.service import IdentityService, MemberSummary

#: Which employment states may follow which.
#:
#: Held as data for the same reason the treatment plan's are: a refusal can
#: then name what *would* have been allowed, instead of the caller guessing.
#: `ENDED` is final — somebody who comes back is re-hired, which is a new
#: employment with its own start date, not a resurrection of the old one that
#: would leave a gap nobody can see in the record.
STATUS_TRANSITIONS: dict[EmploymentStatus, frozenset[EmploymentStatus]] = {
    EmploymentStatus.PROSPECTIVE: frozenset({EmploymentStatus.ACTIVE, EmploymentStatus.ENDED}),
    EmploymentStatus.ACTIVE: frozenset({EmploymentStatus.SUSPENDED, EmploymentStatus.ENDED}),
    EmploymentStatus.SUSPENDED: frozenset({EmploymentStatus.ACTIVE, EmploymentStatus.ENDED}),
    EmploymentStatus.ENDED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ScheduleDayInput:
    weekday: int
    hours: Decimal


def display_name(employee: Employee) -> str:
    parts = [employee.family_name, employee.given_name, employee.patronymic or ""]
    return " ".join(p for p in parts if p).strip()


class EmployeeService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ------------------------------------------------------------- positions

    async def list_positions(
        self, *, organization_id: uuid.UUID, include_inactive: bool = False
    ) -> list[JobPosition]:
        stmt = select(JobPosition).where(JobPosition.organization_id == organization_id)
        if not include_inactive:
            stmt = stmt.where(JobPosition.is_active.is_(True))
        return list((await self._session.execute(stmt.order_by(JobPosition.name))).scalars().all())

    async def get_position(
        self, *, organization_id: uuid.UUID, position_id: uuid.UUID
    ) -> JobPosition:
        stmt = select(JobPosition).where(
            JobPosition.organization_id == organization_id,
            JobPosition.id == position_id,
        )
        position = (await self._session.execute(stmt)).scalar_one_or_none()
        if position is None:
            raise NotFound("Position not found")
        return position

    async def create_position(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        description: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> JobPosition:
        code = code.strip().upper()
        name = name.strip()
        if not code or not name:
            raise ValidationFailed("A position needs a code and a name")

        existing = (
            await self._session.execute(
                select(JobPosition).where(
                    JobPosition.organization_id == organization_id,
                    JobPosition.code == code,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(
                f"A position with code {code!r} already exists",
                details={"code": code},
            )

        position = JobPosition(
            organization_id=organization_id,
            code=code,
            name=name,
            description=description,
        )
        self._session.add(position)
        await self._session.flush()

        self._audit.record(
            events.POSITION_CREATED,
            organization_id=organization_id,
            target_type="job_position",
            target_id=position.id,
            target_label=position.code,
            actor_user_id=actor_user_id,
            new_values={"code": code, "name": name},
        )
        return position

    # ------------------------------------------------------------- employees

    async def list_employees(
        self,
        *,
        organization_id: uuid.UUID,
        statuses: list[EmploymentStatus] | None = None,
        query: str | None = None,
    ) -> list[Employee]:
        stmt = select(Employee).where(Employee.organization_id == organization_id)
        if statuses:
            stmt = stmt.where(Employee.status.in_(statuses))
        if query and query.strip():
            needle = f"%{query.strip().lower()}%"
            # Name and code only. Searching the telephone column would let a
            # digit string in a name field match somebody's number, which is
            # the defect the patient search already fixed once.
            stmt = stmt.where(
                (Employee.family_name.ilike(needle))
                | (Employee.given_name.ilike(needle))
                | (Employee.code.ilike(needle))
            )
        stmt = stmt.order_by(Employee.family_name, Employee.given_name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, *, organization_id: uuid.UUID, employee_id: uuid.UUID) -> Employee:
        stmt = select(Employee).where(
            Employee.organization_id == organization_id,
            Employee.id == employee_id,
        )
        employee = (await self._session.execute(stmt)).scalar_one_or_none()
        if employee is None:
            raise NotFound("Employee not found")
        return employee

    async def _check_user_link(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, exclude: uuid.UUID | None = None
    ) -> None:
        """A login may be linked to one employee, and must belong here.

        The membership question is asked of the identity module rather than
        answered here. Without it an employee record could point at a user
        from another organization, and this organization's payroll would name
        somebody else's staff — but *who belongs where* is identity's
        invariant, and this module borrowing its tables would be a second
        place that decides it.
        """
        identity = IdentityService(self._session, self._audit)
        if not await identity.is_member(user_id=user_id, organization_id=organization_id):
            raise ValidationFailed(
                "That user is not a member of this organization",
                details={"user_id": str(user_id)},
            )

        stmt = select(Employee).where(
            Employee.organization_id == organization_id,
            Employee.user_id == user_id,
        )
        if exclude is not None:
            stmt = stmt.where(Employee.id != exclude)
        clash = (await self._session.execute(stmt)).scalar_one_or_none()
        if clash is not None:
            raise Conflict(
                f"{display_name(clash)} ({clash.code}) is already linked to that login",
                details={"employee_id": str(clash.id)},
            )

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        family_name: str,
        given_name: str,
        hired_on: date | None = None,
        patronymic: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        position_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        notes: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Employee:
        family_name = family_name.strip()
        given_name = given_name.strip()
        if not family_name or not given_name:
            raise ValidationFailed("An employee needs a family name and a given name")

        hired_on = hired_on or clinic_today()
        if position_id is not None:
            await self.get_position(organization_id=organization_id, position_id=position_id)
        if user_id is not None:
            await self._check_user_link(organization_id=organization_id, user_id=user_id)

        employee = Employee(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="employee",
                prefix="EMP-",
                padding=4,
            ),
            user_id=user_id,
            position_id=position_id,
            family_name=family_name,
            given_name=given_name,
            patronymic=(patronymic or "").strip() or None,
            phone=(phone or "").strip() or None,
            address=(address or "").strip() or None,
            # A record created before the first day is PROSPECTIVE; one
            # created on or after it is already working. Guessing neither
            # would mean every hire needs a second call to be useful.
            status=(
                EmploymentStatus.ACTIVE
                if hired_on <= clinic_today()
                else EmploymentStatus.PROSPECTIVE
            ),
            hired_on=hired_on,
            notes=notes,
        )
        self._session.add(employee)
        await self._session.flush()

        self._audit.record(
            events.EMPLOYEE_CREATED,
            organization_id=organization_id,
            target_type="employee",
            target_id=employee.id,
            target_label=employee.code,
            business_event=f"employee:{employee.id}",
            actor_user_id=actor_user_id,
            new_values={
                "name": display_name(employee),
                "hired_on": hired_on,
                "status": employee.status.value,
                "user_id": user_id,
            },
        )
        return employee

    async def update(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        changes: dict[str, object],
        actor_user_id: uuid.UUID | None = None,
    ) -> Employee:
        """Edit the details. Status and the leaving date are not among them.

        Ending an employment goes through `end_employment`, which demands a
        date and a reason and writes an always-audited record. Letting it
        happen through a general-purpose edit would make it indistinguishable
        from correcting a telephone number.
        """
        employee = await self.get(organization_id=organization_id, employee_id=employee_id)
        editable = {
            "family_name",
            "given_name",
            "patronymic",
            "phone",
            "address",
            "position_id",
            "user_id",
            "notes",
        }
        unknown = set(changes) - editable
        if unknown:
            raise ValidationFailed(
                f"These fields cannot be edited here: {', '.join(sorted(unknown))}",
                details={"fields": sorted(unknown)},
            )
        if employee.status is EmploymentStatus.ENDED:
            raise Conflict(
                "That employment has ended. The record is kept as it was.",
                details={"status": employee.status.value},
            )

        before = {field: getattr(employee, field) for field in editable}

        if changes.get("position_id") is not None:
            await self.get_position(
                organization_id=organization_id,
                position_id=changes["position_id"],  # type: ignore[arg-type]
            )
        if changes.get("user_id") is not None:
            await self._check_user_link(
                organization_id=organization_id,
                user_id=changes["user_id"],  # type: ignore[arg-type]
                exclude=employee.id,
            )

        for field, value in changes.items():
            if isinstance(value, str):
                value = value.strip() or None
                if field in {"family_name", "given_name"} and value is None:
                    raise ValidationFailed(f"{field} cannot be blank")
            setattr(employee, field, value)
        await self._session.flush()

        after = {field: getattr(employee, field) for field in editable}
        old_values, new_values = changed_fields(before, after)
        if old_values or new_values:
            self._audit.record(
                events.EMPLOYEE_UPDATED,
                organization_id=organization_id,
                target_type="employee",
                target_id=employee.id,
                target_label=employee.code,
                business_event=f"employee:{employee.id}",
                actor_user_id=actor_user_id,
                old_values=old_values,
                new_values=new_values,
            )
        return employee

    async def set_status(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        status: EmploymentStatus,
        actor_user_id: uuid.UUID | None = None,
    ) -> Employee:
        """Start or suspend somebody. Ending goes through `end_employment`."""
        if status is EmploymentStatus.ENDED:
            raise ValidationFailed(
                "Ending an employment needs a leaving date and a reason",
                details={"use": "end_employment"},
            )
        employee = await self.get(organization_id=organization_id, employee_id=employee_id)
        if employee.status is status:
            return employee
        allowed = STATUS_TRANSITIONS.get(employee.status, frozenset())
        if status not in allowed:
            raise Conflict(
                f"An employment that is {employee.status.value.lower()} cannot become "
                f"{status.value.lower()}",
                details={
                    "status": employee.status.value,
                    "allowed": sorted(s.value for s in allowed),
                },
            )

        previous = employee.status
        employee.status = status
        await self._session.flush()

        self._audit.record(
            events.EMPLOYEE_UPDATED,
            organization_id=organization_id,
            target_type="employee",
            target_id=employee.id,
            target_label=employee.code,
            business_event=f"employee:{employee.id}",
            actor_user_id=actor_user_id,
            old_values={"status": previous.value},
            new_values={"status": status.value},
        )
        return employee

    async def end_employment(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        ended_on: date,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> Employee:
        """Record that somebody has left.

        Deliberately its own operation, with a mandatory reason and an audit
        record that is written whatever else is not. It is financially
        material — a payroll run will stop paying them — and personally
        consequential, which is exactly the combination the audit trail
        exists for.

        The record is kept, never deleted. Payslips, attendance and anything
        they signed still point at it.
        """
        if not reason or not reason.strip():
            raise ValidationFailed("Ending an employment requires a reason")
        employee = await self.get(organization_id=organization_id, employee_id=employee_id)
        if employee.status is EmploymentStatus.ENDED:
            raise Conflict(
                f"That employment already ended on {employee.ended_on}",
                details={"ended_on": str(employee.ended_on)},
            )
        if ended_on < employee.hired_on:
            raise ValidationFailed(
                "Somebody cannot leave before they started",
                details={"hired_on": str(employee.hired_on), "ended_on": str(ended_on)},
            )

        previous = employee.status
        employee.status = EmploymentStatus.ENDED
        employee.ended_on = ended_on
        employee.ended_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            events.EMPLOYMENT_ENDED,
            organization_id=organization_id,
            target_type="employee",
            target_id=employee.id,
            target_label=employee.code,
            business_event=f"employee:{employee.id}",
            actor_user_id=actor_user_id,
            reason=reason.strip(),
            old_values={"status": previous.value},
            new_values={"status": EmploymentStatus.ENDED.value, "ended_on": ended_on},
        )
        return employee

    # -------------------------------------------------------------- the rota

    async def get_schedule(
        self, *, organization_id: uuid.UUID, employee_id: uuid.UUID
    ) -> list[WorkScheduleDay]:
        await self.get(organization_id=organization_id, employee_id=employee_id)
        stmt = (
            select(WorkScheduleDay)
            .where(
                WorkScheduleDay.organization_id == organization_id,
                WorkScheduleDay.employee_id == employee_id,
            )
            .order_by(WorkScheduleDay.weekday)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def set_schedule(
        self,
        *,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        days: list[ScheduleDayInput],
        actor_user_id: uuid.UUID | None = None,
    ) -> list[WorkScheduleDay]:
        """Replace somebody's ordinary week.

        Replace rather than merge: a rota is read as a whole — "Tuesday and
        Thursday" — and a merge would leave a day nobody meant to keep. An
        empty list is a valid rota, and means somebody with no fixed days.
        """
        employee = await self.get(organization_id=organization_id, employee_id=employee_id)
        if employee.status is EmploymentStatus.ENDED:
            raise Conflict("That employment has ended; its rota is not changed")

        seen: set[int] = set()
        for day in days:
            if day.weekday < 1 or day.weekday > 7:
                raise ValidationFailed(
                    "A weekday runs from 1 (Monday) to 7 (Sunday)",
                    details={"weekday": day.weekday},
                )
            if day.weekday in seen:
                raise ValidationFailed(
                    "The same weekday appears twice in that rota",
                    details={"weekday": day.weekday},
                )
            seen.add(day.weekday)
            if day.hours <= 0 or day.hours > 24:
                raise ValidationFailed(
                    "Hours on a rostered day must be above nothing and at most 24",
                    details={"weekday": day.weekday, "hours": str(day.hours)},
                )

        existing = await self.get_schedule(organization_id=organization_id, employee_id=employee_id)
        before = {str(d.weekday): str(d.hours) for d in existing}
        for row in existing:
            await self._session.delete(row)
        await self._session.flush()

        created = [
            WorkScheduleDay(
                organization_id=organization_id,
                employee_id=employee_id,
                weekday=day.weekday,
                hours=day.hours,
            )
            for day in sorted(days, key=lambda d: d.weekday)
        ]
        for row in created:
            self._session.add(row)
        await self._session.flush()

        after = {str(d.weekday): str(d.hours) for d in created}
        if before != after:
            self._audit.record(
                events.SCHEDULE_CHANGED,
                organization_id=organization_id,
                target_type="employee",
                target_id=employee.id,
                target_label=employee.code,
                business_event=f"employee:{employee.id}",
                actor_user_id=actor_user_id,
                old_values={"rota": before},
                new_values={"rota": after},
            )
        return created

    async def scheduled_hours(
        self, *, organization_id: uuid.UUID, employee_id: uuid.UUID, on: date
    ) -> Decimal | None:
        """What the rota says this person's `on` should be, if anything."""
        rota = await self.get_schedule(organization_id=organization_id, employee_id=employee_id)
        for day in rota:
            if day.weekday == on.isoweekday():
                return day.hours
        return None

    # ----------------------------------------------------------- directory

    async def linkable_users(self, *, organization_id: uuid.UUID) -> list[MemberSummary]:
        """Members of this organization with no employee record yet.

        Offered when linking a person to their login. Somebody already linked
        is left out rather than shown and refused. The directory itself comes
        from identity; this module only knows which of them are spoken for.
        """
        taken = (
            (
                await self._session.execute(
                    select(Employee.user_id).where(
                        Employee.organization_id == organization_id,
                        Employee.user_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        identity = IdentityService(self._session, self._audit)
        return await identity.list_members(
            organization_id=organization_id,
            exclude_user_ids=[u for u in taken if u is not None],
        )

    async def role_codes_for(self, *, organization_id: uuid.UUID, employee: Employee) -> list[str]:
        """The roles this employee's login holds, read from the membership.

        Never stored on the employee. One person may hold several roles, and
        a copy here would be a second answer to what they may do — while the
        first answer is the one the API actually enforces against.
        """
        if employee.user_id is None:
            return []
        identity = IdentityService(self._session, self._audit)
        return await identity.role_codes_for(
            user_id=employee.user_id, organization_id=organization_id
        )


__all__ = [
    "STATUS_TRANSITIONS",
    "AttendanceDay",
    "EmployeeService",
    "ScheduleDayInput",
    "display_name",
]
