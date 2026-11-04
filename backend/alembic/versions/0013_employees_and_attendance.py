"""employees, positions, the rota and attendance

Phase 5, first half. Employees come before payroll because payroll needs
somebody to pay, before documents because an employee document needs an
employee, and before purchasing can record who asked for something.

Four tables, and what is *not* in them is the point:

* `employee` does not duplicate `app_user`. A login is how somebody signs in;
  an employee is somebody the clinic employs. `employee.user_id` links them
  and is nullable, because a person can be paid without ever signing in — and
  it is unique per organization, so one person cannot end up with two records
  and therefore two payslips.
* No roles here. One employee holding several roles already works: roles hang
  off the membership and `membership_role` has always been many-to-many.
  Re-implementing that on the employee would create a second, disagreeing
  answer to "what may this person do".
* **No money.** No salary, no rate, no accrual. Compensation is payroll's and
  reaches the ledger through the posting policy. A wage column here would be
  the start of a second payroll ledger.

`job_position` rather than `position`: `position` is a function name in
PostgreSQL and reserved in the standard, so a table called that works only
because SQLAlchemy quotes it — a trap for anybody writing a query by hand.

Two check constraints are worth naming, because they make a wrong row
unrepresentable rather than merely discouraged:

* `ck_employee_ended_has_date` — an employment is `ENDED` exactly when it has
  a leaving date. Without it a record could read `ACTIVE` with a date in the
  past, or `ENDED` with none, and a payroll run would have to guess.
* `ck_attendance_day_hours_match_code` — hours belong to a day that was
  worked. Six hours on a day marked sick is a contradiction the database
  refuses outright.

And one unique constraint: `uq_attendance_day_employee_date`, one row per
person per day. A second row for the same day is the bug that pays somebody
twice, and it is cheap to make impossible.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-03 17:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_position",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_job_position_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_position")),
        sa.UniqueConstraint("organization_id", "code", name="uq_job_position_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_job_position_organization_id_id"),
    )
    op.create_index(
        op.f("ix_job_position_organization_id"), "job_position", ["organization_id"], unique=False
    )
    op.create_table(
        "employee",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("position_id", sa.UUID(), nullable=True),
        sa.Column("family_name", sa.String(length=128), nullable=False),
        sa.Column("given_name", sa.String(length=128), nullable=False),
        sa.Column("patronymic", sa.String(length=128), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PROSPECTIVE",
                "ACTIVE",
                "SUSPENDED",
                "ENDED",
                name="employment_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("hired_on", sa.Date(), nullable=False),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("ended_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'ENDED') = (ended_on IS NOT NULL)",
            name="ck_employee_ended_has_date",
        ),
        sa.CheckConstraint(
            "ended_on IS NULL OR ended_on >= hired_on",
            name="ck_employee_ended_after_hired",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "position_id"],
            ["job_position.organization_id", "job_position.id"],
            name="fk_employee_position",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_employee_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_employee_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee")),
        sa.UniqueConstraint("organization_id", "code", name="uq_employee_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_employee_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_employee_organization_id_user_id"
        ),
    )
    op.create_index(
        op.f("ix_employee_organization_id"), "employee", ["organization_id"], unique=False
    )
    op.create_index("ix_employee_status", "employee", ["organization_id", "status"], unique=False)
    op.create_table(
        "attendance_day",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column(
            "code",
            sa.Enum(
                "WORKED",
                "ABSENT",
                "SICK",
                "LEAVE",
                "HOLIDAY",
                name="attendance_code",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("hours", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(code = 'WORKED' AND hours IS NOT NULL AND hours > 0 AND hours <= 24)"
            " OR (code <> 'WORKED' AND hours IS NULL)",
            name="ck_attendance_day_hours_match_code",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "employee_id"],
            ["employee.organization_id", "employee.id"],
            name="fk_attendance_day_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_attendance_day_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_attendance_day_recorded_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_day")),
        sa.UniqueConstraint(
            "organization_id", "employee_id", "on_date", name="uq_attendance_day_employee_date"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_attendance_day_organization_id_id"),
    )
    op.create_index(
        "ix_attendance_day_date", "attendance_day", ["organization_id", "on_date"], unique=False
    )
    op.create_index(
        op.f("ix_attendance_day_organization_id"),
        "attendance_day",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "work_schedule_day",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "hours > 0 AND hours <= 24",
            name="ck_work_schedule_day_hours",
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 1 AND 7",
            name="ck_work_schedule_day_weekday",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "employee_id"],
            ["employee.organization_id", "employee.id"],
            name="fk_work_schedule_day_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_work_schedule_day_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_schedule_day")),
        sa.UniqueConstraint(
            "organization_id",
            "employee_id",
            "weekday",
            name="uq_work_schedule_day_employee_weekday",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_work_schedule_day_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_work_schedule_day_organization_id"),
        "work_schedule_day",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_work_schedule_day_organization_id"), table_name="work_schedule_day")
    op.drop_table("work_schedule_day")
    op.drop_index(op.f("ix_attendance_day_organization_id"), table_name="attendance_day")
    op.drop_index("ix_attendance_day_date", table_name="attendance_day")
    op.drop_table("attendance_day")
    op.drop_index("ix_employee_status", table_name="employee")
    op.drop_index(op.f("ix_employee_organization_id"), table_name="employee")
    op.drop_table("employee")
    op.drop_index(op.f("ix_job_position_organization_id"), table_name="job_position")
    op.drop_table("job_position")
