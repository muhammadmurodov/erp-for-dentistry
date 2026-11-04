"""Request and response shapes for employees and attendance."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.hr.enums import AttendanceCode, EmploymentStatus


def _reject_float(value: object) -> object:
    """Hours are decimals. A float reaches a payslip as 7.699999999999999."""
    if isinstance(value, float):
        raise ValueError("Pass hours as a string or a decimal, not a binary float")
    return value


# ------------------------------------------------------------------ positions


class PositionCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool


# ------------------------------------------------------------------ employees


class EmployeeCreateRequest(BaseModel):
    family_name: str = Field(min_length=1, max_length=128)
    given_name: str = Field(min_length=1, max_length=128)
    patronymic: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = None
    position_id: uuid.UUID | None = None
    #: The login this person uses, when they have one. Linking is allowed
    #: here; creating the login is not — that is `identity.user.manage`.
    user_id: uuid.UUID | None = None
    hired_on: date | None = None
    notes: str | None = None


class EmployeeUpdateRequest(BaseModel):
    """Only the details. Status and leaving go through their own endpoints."""

    model_config = ConfigDict(extra="forbid")

    family_name: str | None = Field(default=None, min_length=1, max_length=128)
    given_name: str | None = Field(default=None, min_length=1, max_length=128)
    patronymic: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = None
    position_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    notes: str | None = None


class EmployeeStatusRequest(BaseModel):
    status: EmploymentStatus


class EndEmploymentRequest(BaseModel):
    ended_on: date
    reason: str = Field(min_length=3)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str
    family_name: str
    given_name: str
    patronymic: str | None
    phone: str | None
    address: str | None
    position_id: uuid.UUID | None
    position_name: str | None
    user_id: uuid.UUID | None
    #: The roles the linked login holds, read from the membership rather than
    #: stored here. One person may hold several — the clinic's owner is also
    #: its dentist — and this module never becomes a second answer to that.
    role_codes: list[str]
    status: EmploymentStatus
    hired_on: date
    ended_on: date | None
    ended_reason: str | None
    notes: str | None


class LinkableUserRow(BaseModel):
    """A member of this organization with no employee record yet."""

    id: uuid.UUID
    email: str
    full_name: str


# ----------------------------------------------------------------- the rota


class ScheduleDay(BaseModel):
    #: ISO weekday: 1 Monday … 7 Sunday.
    weekday: int = Field(ge=1, le=7)
    hours: Decimal = Field(gt=0, le=24)

    @field_validator("hours", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class ScheduleRequest(BaseModel):
    """The whole week, replacing whatever was there. An empty list is valid."""

    days: list[ScheduleDay]


# --------------------------------------------------------------- attendance


class AttendanceRecordRequest(BaseModel):
    employee_id: uuid.UUID
    on_date: date
    code: AttendanceCode
    hours: Decimal | None = Field(default=None, gt=0, le=24)
    note: str | None = None

    @field_validator("hours", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class AttendanceDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    on_date: date
    code: AttendanceCode
    hours: Decimal | None
    note: str | None
    recorded_by_user_id: uuid.UUID | None


class AttendanceSummaryResponse(BaseModel):
    employee_id: uuid.UUID
    from_date: date
    to_date: date
    days_present: int
    days_absent: int
    days_sick: int
    days_leave: int
    days_holiday: int
    hours: Decimal
