"""Employees, their positions, their rota and their attendance.

Three rules shape what is here.

**An employee is not a user, and neither replaces the other.** `app_user` is
how somebody signs in; `employee` is the person the clinic employs. In a
family clinic the same human is usually both, and `employee.user_id` links
them — but a cleaner who is paid and never signs in is an employee with no
user, and a visiting accountant with a login may be no employee at all.
Duplicating the name and the roles onto the employee row would create a
second directory free to disagree with the first.

**Roles stay on the membership.** The brief asks that one employee be able to
hold several roles; that already works, because roles hang off the
membership and `membership_role` has always allowed many. Nothing here
re-implements it. An employee with no user simply has no roles, which is
correct — they cannot act in the system at all.

**This module stores no money.** No salary column, no accrual, no balance.
Compensation is payroll's, and payroll reaches the ledger through the posting
policy like everything else. An employee row carrying a wage would be the
beginning of a second payroll ledger.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.hr.enums import AttendanceCode, EmploymentStatus


def _enum(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _org_scope(table: str) -> tuple:
    return (UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id"),)


def _org_column() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class JobPosition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A job title: dentist, receptionist, cleaner.

    Deliberately not the same thing as a role. A role is authority inside
    this system; a position is what the person is employed as, and the two
    genuinely differ — the clinic's owner holds the `owner` role and is
    employed as a dentist.
    """

    __tablename__ = "job_position"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        *_org_scope("job_position"),
        UniqueConstraint("organization_id", "code", name="uq_job_position_organization_id_code"),
    )


class Employee(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Somebody the clinic employs."""

    __tablename__ = "employee"

    organization_id: Mapped[uuid.UUID] = _org_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The login this person uses, when they have one. Nullable on purpose:
    #: somebody can be paid without ever signing in.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    family_name: Mapped[str] = mapped_column(String(128), nullable=False)
    given_name: Mapped[str] = mapped_column(String(128), nullable=False)
    patronymic: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    #: Deliberately short and free-text. A clinic of three does not need a
    #: structured address, and a structured one it never fills in is worse.
    address: Mapped[str | None] = mapped_column(Text)

    status: Mapped[EmploymentStatus] = mapped_column(
        _enum(EmploymentStatus, "employment_status"),
        nullable=False,
        default=EmploymentStatus.PROSPECTIVE,
    )
    hired_on: Mapped[date] = mapped_column(Date, nullable=False)
    ended_on: Mapped[date | None] = mapped_column(Date)
    ended_reason: Mapped[str | None] = mapped_column(Text)

    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("employee"),
        UniqueConstraint("organization_id", "code", name="uq_employee_organization_id_code"),
        # One employee record per login. Two rows for the same person would
        # produce two payslips, which is the failure worth a constraint.
        UniqueConstraint("organization_id", "user_id", name="uq_employee_organization_id_user_id"),
        ForeignKeyConstraint(
            ["organization_id", "position_id"],
            ["job_position.organization_id", "job_position.id"],
            ondelete="RESTRICT",
            name="fk_employee_position",
        ),
        CheckConstraint(
            "ended_on IS NULL OR ended_on >= hired_on",
            name="ck_employee_ended_after_hired",
        ),
        # An ended employment has a leaving date, and only an ended one has.
        # Without this a record could read ACTIVE with a date in the past, or
        # ENDED with no date, and a payroll run would have to guess.
        CheckConstraint(
            "(status = 'ENDED') = (ended_on IS NOT NULL)",
            name="ck_employee_ended_has_date",
        ),
        Index("ix_employee_status", "organization_id", "status"),
    )


class WorkScheduleDay(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One weekday of somebody's ordinary week.

    A rota rather than a contract: it says what a normal Tuesday looks like,
    which is what a rota is for and what attendance is compared against. It
    is not evidence of anything — the attendance record is.
    """

    __tablename__ = "work_schedule_day"

    organization_id: Mapped[uuid.UUID] = _org_column()
    employee_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: ISO weekday: 1 Monday … 7 Sunday. Python's `date.isoweekday()`.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    __table_args__ = (
        *_org_scope("work_schedule_day"),
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "weekday",
            name="uq_work_schedule_day_employee_weekday",
        ),
        ForeignKeyConstraint(
            ["organization_id", "employee_id"],
            ["employee.organization_id", "employee.id"],
            ondelete="RESTRICT",
            name="fk_work_schedule_day_employee",
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_work_schedule_day_weekday"),
        CheckConstraint("hours > 0 AND hours <= 24", name="ck_work_schedule_day_hours"),
    )


class AttendanceDay(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What one person's one day actually was.

    One row per employee per day, enforced by a unique constraint rather than
    left to the application: a second row for the same day is the bug that
    pays somebody twice, and it is cheap to make unrepresentable.
    """

    __tablename__ = "attendance_day"

    organization_id: Mapped[uuid.UUID] = _org_column()
    employee_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    on_date: Mapped[date] = mapped_column(Date, nullable=False)
    code: Mapped[AttendanceCode] = mapped_column(
        _enum(AttendanceCode, "attendance_code"), nullable=False
    )
    hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("attendance_day"),
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "on_date",
            name="uq_attendance_day_employee_date",
        ),
        ForeignKeyConstraint(
            ["organization_id", "employee_id"],
            ["employee.organization_id", "employee.id"],
            ondelete="RESTRICT",
            name="fk_attendance_day_employee",
        ),
        # Hours belong to a day that was worked. A sick day with six hours on
        # it is a contradiction the database can refuse outright, and does.
        CheckConstraint(
            "(code = 'WORKED' AND hours IS NOT NULL AND hours > 0 AND hours <= 24)"
            " OR (code <> 'WORKED' AND hours IS NULL)",
            name="ck_attendance_day_hours_match_code",
        ),
        Index("ix_attendance_day_date", "organization_id", "on_date"),
    )
