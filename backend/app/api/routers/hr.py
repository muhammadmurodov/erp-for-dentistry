"""Employee, rota and attendance endpoints.

Nothing here posts to the ledger. An employee record is not a financial
event: it names somebody the clinic employs, and what they cost reaches the
books through payroll, under the posting policy, like everything else.

Three authority boundaries worth stating, because they are the reason the
endpoints are split the way they are:

* **Reading an employee** (`hr.employee.read`) is separate from **editing
  one** (`hr.employee.manage`), which is sensitive and audited.
* **Recording attendance** (`hr.attendance.record`) is its own permission,
  not part of managing an employee. Marking a day is a daily clerical act;
  changing somebody's employment is not, and the clinic should be able to
  let one person do the first without the second.
* **Linking an employee to a login** is allowed here; **creating** the login
  is not. Hiring somebody and giving them authority over the books are two
  decisions, and a single endpoint that did both would let the HR permission
  mint an account with roles on it.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.modules.hr.enums import EmploymentStatus
from app.modules.hr.models import Employee, JobPosition
from app.modules.hr.schemas import (
    AttendanceDayResponse,
    AttendanceRecordRequest,
    AttendanceSummaryResponse,
    EmployeeCreateRequest,
    EmployeeResponse,
    EmployeeStatusRequest,
    EmployeeUpdateRequest,
    EndEmploymentRequest,
    LinkableUserRow,
    PositionCreateRequest,
    PositionResponse,
    ScheduleDay,
    ScheduleRequest,
)
from app.modules.hr.services.attendance import AttendanceService
from app.modules.hr.services.employees import (
    EmployeeService,
    ScheduleDayInput,
    display_name,
)

router = APIRouter(prefix="/hr", tags=["hr"])


async def _employee_response(
    employee: Employee,
    *,
    db: DbSession,
    audit: AuditDep,
    positions: dict[uuid.UUID, JobPosition],
) -> EmployeeResponse:
    """One employee, with the roles their login holds.

    The roles are read from the membership every time rather than copied onto
    the employee row. A copy would be a second answer to "what may this person
    do", and the first answer is the one the API actually enforces against.
    """
    role_codes = await EmployeeService(db, audit).role_codes_for(
        organization_id=employee.organization_id, employee=employee
    )

    position = positions.get(employee.position_id) if employee.position_id else None
    return EmployeeResponse(
        id=employee.id,
        code=employee.code,
        display_name=display_name(employee),
        family_name=employee.family_name,
        given_name=employee.given_name,
        patronymic=employee.patronymic,
        phone=employee.phone,
        address=employee.address,
        position_id=employee.position_id,
        position_name=position.name if position else None,
        user_id=employee.user_id,
        role_codes=role_codes,
        status=employee.status,
        hired_on=employee.hired_on,
        ended_on=employee.ended_on,
        ended_reason=employee.ended_reason,
        notes=employee.notes,
    )


async def _positions_by_id(
    *, db: DbSession, audit: AuditDep, organization_id: uuid.UUID
) -> dict[uuid.UUID, JobPosition]:
    rows = await EmployeeService(db, audit).list_positions(
        organization_id=organization_id, include_inactive=True
    )
    return {p.id: p for p in rows}


# ------------------------------------------------------------------ positions


@router.get(
    "/positions",
    response_model=list[PositionResponse],
    dependencies=[Depends(require_permissions("hr.employee.read"))],
)
async def list_positions(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    include_inactive: bool = Query(default=False),
) -> list[PositionResponse]:
    rows = await EmployeeService(db, audit).list_positions(
        organization_id=context.require_organization(),
        include_inactive=include_inactive,
    )
    return [PositionResponse.model_validate(p) for p in rows]


@router.post(
    "/positions",
    response_model=PositionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def create_position(
    payload: PositionCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PositionResponse:
    position = await EmployeeService(db, audit).create_position(
        organization_id=context.require_organization(),
        code=payload.code,
        name=payload.name,
        description=payload.description,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return PositionResponse.model_validate(position)


# ------------------------------------------------------------------ employees


@router.get(
    "/employees",
    response_model=list[EmployeeResponse],
    dependencies=[Depends(require_permissions("hr.employee.read"))],
)
async def list_employees(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    q: str | None = Query(default=None, max_length=128),
    status_filter: list[EmploymentStatus] | None = Query(default=None, alias="status"),
) -> list[EmployeeResponse]:
    organization_id = context.require_organization()
    rows = await EmployeeService(db, audit).list_employees(
        organization_id=organization_id,
        statuses=list(status_filter) if status_filter else None,
        query=q,
    )
    positions = await _positions_by_id(db=db, audit=audit, organization_id=organization_id)
    return [await _employee_response(e, db=db, audit=audit, positions=positions) for e in rows]


@router.get(
    "/employees/linkable-users",
    response_model=list[LinkableUserRow],
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def linkable_users(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[LinkableUserRow]:
    """Members with no employee record yet, for the link control.

    Somebody already linked is left out rather than offered and refused.
    """
    users = await EmployeeService(db, audit).linkable_users(
        organization_id=context.require_organization()
    )
    return [LinkableUserRow(id=u.user_id, email=u.email, full_name=u.full_name) for u in users]


@router.get(
    "/employees/{employee_id}",
    response_model=EmployeeResponse,
    dependencies=[Depends(require_permissions("hr.employee.read"))],
)
async def get_employee(
    employee_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EmployeeResponse:
    organization_id = context.require_organization()
    employee = await EmployeeService(db, audit).get(
        organization_id=organization_id, employee_id=employee_id
    )
    positions = await _positions_by_id(db=db, audit=audit, organization_id=organization_id)
    return await _employee_response(employee, db=db, audit=audit, positions=positions)


@router.post(
    "/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def create_employee(
    payload: EmployeeCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EmployeeResponse:
    organization_id = context.require_organization()
    employee = await EmployeeService(db, audit).create(
        organization_id=organization_id,
        family_name=payload.family_name,
        given_name=payload.given_name,
        patronymic=payload.patronymic,
        phone=payload.phone,
        address=payload.address,
        position_id=payload.position_id,
        user_id=payload.user_id,
        hired_on=payload.hired_on,
        notes=payload.notes,
        actor_user_id=context.user_id,
    )
    await db.commit()
    positions = await _positions_by_id(db=db, audit=audit, organization_id=organization_id)
    return await _employee_response(employee, db=db, audit=audit, positions=positions)


@router.patch(
    "/employees/{employee_id}",
    response_model=EmployeeResponse,
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def update_employee(
    employee_id: uuid.UUID,
    payload: EmployeeUpdateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EmployeeResponse:
    organization_id = context.require_organization()
    employee = await EmployeeService(db, audit).update(
        organization_id=organization_id,
        employee_id=employee_id,
        changes=payload.model_dump(exclude_unset=True),
        actor_user_id=context.user_id,
    )
    await db.commit()
    positions = await _positions_by_id(db=db, audit=audit, organization_id=organization_id)
    return await _employee_response(employee, db=db, audit=audit, positions=positions)


@router.post(
    "/employees/{employee_id}/status",
    response_model=EmployeeResponse,
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def set_employee_status(
    employee_id: uuid.UUID,
    payload: EmployeeStatusRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EmployeeResponse:
    """Start somebody, or suspend them. Ending has its own endpoint."""
    organization_id = context.require_organization()
    employee = await EmployeeService(db, audit).set_status(
        organization_id=organization_id,
        employee_id=employee_id,
        status=payload.status,
        actor_user_id=context.user_id,
    )
    await db.commit()
    positions = await _positions_by_id(db=db, audit=audit, organization_id=organization_id)
    return await _employee_response(employee, db=db, audit=audit, positions=positions)


@router.post(
    "/employees/{employee_id}/end",
    response_model=EmployeeResponse,
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def end_employment(
    employee_id: uuid.UUID,
    payload: EndEmploymentRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EmployeeResponse:
    """Record that somebody has left. The record is kept, never deleted."""
    organization_id = context.require_organization()
    employee = await EmployeeService(db, audit).end_employment(
        organization_id=organization_id,
        employee_id=employee_id,
        ended_on=payload.ended_on,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    positions = await _positions_by_id(db=db, audit=audit, organization_id=organization_id)
    return await _employee_response(employee, db=db, audit=audit, positions=positions)


# ------------------------------------------------------------------- the rota


@router.get(
    "/employees/{employee_id}/schedule",
    response_model=list[ScheduleDay],
    dependencies=[Depends(require_permissions("hr.employee.read"))],
)
async def get_schedule(
    employee_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[ScheduleDay]:
    rows = await EmployeeService(db, audit).get_schedule(
        organization_id=context.require_organization(), employee_id=employee_id
    )
    return [ScheduleDay(weekday=r.weekday, hours=r.hours) for r in rows]


@router.put(
    "/employees/{employee_id}/schedule",
    response_model=list[ScheduleDay],
    dependencies=[Depends(require_permissions("hr.employee.manage"))],
)
async def set_schedule(
    employee_id: uuid.UUID,
    payload: ScheduleRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[ScheduleDay]:
    """Replace the ordinary week. An empty list means no fixed days."""
    rows = await EmployeeService(db, audit).set_schedule(
        organization_id=context.require_organization(),
        employee_id=employee_id,
        days=[ScheduleDayInput(weekday=d.weekday, hours=d.hours) for d in payload.days],
        actor_user_id=context.user_id,
    )
    await db.commit()
    return [ScheduleDay(weekday=r.weekday, hours=r.hours) for r in rows]


# ---------------------------------------------------------------- attendance


@router.get(
    "/attendance",
    response_model=list[AttendanceDayResponse],
    dependencies=[Depends(require_permissions("hr.employee.read"))],
)
async def list_attendance(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    employee_id: uuid.UUID | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
) -> list[AttendanceDayResponse]:
    rows = await AttendanceService(db, audit).list_days(
        organization_id=context.require_organization(),
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
    )
    return [AttendanceDayResponse.model_validate(r) for r in rows]


@router.post(
    "/attendance",
    response_model=AttendanceDayResponse,
    dependencies=[Depends(require_permissions("hr.attendance.record"))],
)
async def record_attendance(
    payload: AttendanceRecordRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AttendanceDayResponse:
    """Record, or correct, one person's one day.

    Recording a day twice overwrites it: a day has one truth and a correction
    is the ordinary case. The previous value goes into the audit record, so
    the correction is visible rather than silent.
    """
    day = await AttendanceService(db, audit).record(
        organization_id=context.require_organization(),
        employee_id=payload.employee_id,
        on=payload.on_date,
        code=payload.code,
        hours=payload.hours,
        note=payload.note,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AttendanceDayResponse.model_validate(day)


@router.delete(
    "/attendance",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permissions("hr.attendance.record"))],
)
async def delete_attendance(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    employee_id: uuid.UUID = Query(),
    on_date: date = Query(),
) -> None:
    """Remove a day recorded by mistake. The removal is audited."""
    await AttendanceService(db, audit).delete_day(
        organization_id=context.require_organization(),
        employee_id=employee_id,
        on=on_date,
        actor_user_id=context.user_id,
    )
    await db.commit()


@router.get(
    "/attendance/summary",
    response_model=AttendanceSummaryResponse,
    dependencies=[Depends(require_permissions("hr.employee.read"))],
)
async def attendance_summary(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    employee_id: uuid.UUID = Query(),
    from_date: date = Query(),
    to_date: date = Query(),
) -> AttendanceSummaryResponse:
    """Total a stretch of days. Derived on every call, never stored."""
    summary = await AttendanceService(db, audit).summarise(
        organization_id=context.require_organization(),
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
    )
    return AttendanceSummaryResponse(**asdict(summary))
