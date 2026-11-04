"""Enumerations for employees and attendance.

Stored as short strings with a check constraint, as everywhere else in this
codebase: a native PostgreSQL enum needs DDL to grow, and the constraint
reads just as well in an error message.
"""

from __future__ import annotations

from enum import StrEnum


class EmploymentStatus(StrEnum):
    """Where somebody stands with the clinic.

    `PROSPECTIVE` exists because a record is often created before the first
    day — a contract signed in advance — and an employee who has not started
    must not appear on a payroll run or a rota.
    """

    PROSPECTIVE = "PROSPECTIVE"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    """Still employed, not currently working. Keeps the record out of a rota
    without ending the employment, which is what a long absence is."""

    ENDED = "ENDED"


#: Statuses in which somebody may be rostered, marked present, or paid.
WORKING_STATUSES = frozenset({EmploymentStatus.ACTIVE})


class AttendanceCode(StrEnum):
    """What one day was.

    These are the system's own codes, not the statutory timesheet letters.
    The clinic's legal timesheet uses twelve single-character codes whose
    Uzbek wording is unconfirmed (`OPEN_QUESTIONS.md` P1), and inventing a
    mapping to them would put a guess on a document somebody signs. These
    five describe what actually happens in a three-person clinic, and a
    mapping can be added beside them once the wording is known.
    """

    WORKED = "WORKED"
    ABSENT = "ABSENT"
    """Away without it being leave or sickness. Unpaid by default."""

    SICK = "SICK"
    LEAVE = "LEAVE"
    """Agreed time off: annual leave, a day for a family matter."""

    HOLIDAY = "HOLIDAY"
    """A public holiday the clinic was closed for."""


#: Codes on which hours may be recorded. Recording six hours worked on a day
#: marked sick is a contradiction, not an edge case.
CODES_WITH_HOURS = frozenset({AttendanceCode.WORKED})

#: Codes that count as time at work when something is derived from
#: attendance. `HOLIDAY` is not among them: the clinic was closed.
PRESENT_CODES = frozenset({AttendanceCode.WORKED})
