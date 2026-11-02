"""What is happening in the clinic today, as one read model.

The dashboard this feeds used to show the organization's registration details
and a list of module statuses — true, but not a thing anybody needs at eight
in the morning. What the owner of a three-person clinic wants to know is: who
is coming in, what is still owed, how much went through the till, and whether
anything needs attention before it becomes a problem.

Two rules shape this file.

**Every figure is derived here, on the server.** A dashboard that sums a
truncated list of payments in the browser will disagree with the ledger, and
the first time it does, nobody trusts the screen again. The outstanding debt
comes from `ReceivablesService`, which is the one definition of what a patient
owes (ADR-026); the drawer's expected cash comes from `CashSessionService`,
which derives it from the movements; nothing is read from a stored total.

**It tells the caller only what the caller may already see.** The receptionist
has no permission to read invoices, so passing `include_money=False` means the
query is not even run — the figures are absent rather than hidden by the
interface. A dashboard is a reporting surface, and a reporting surface that
quietly widens what somebody can see is a permission bug with a chart on it.

It lives in `clinic` because `clinic` is the only module above both `sales`
and `cash` (ADR-030), so it can read a receivable and a drawer without either
module learning about the other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.modules.audit.service import AuditRecorder
from app.modules.cash.services.sessions import CashSessionService
from app.modules.clinic.enums import (
    AppointmentStatus,
    DuplicateReviewStatus,
    TreatmentItemStatus,
)
from app.modules.clinic.models import (
    Appointment,
    Patient,
    PatientDuplicateReview,
    TreatmentItem,
)
from app.modules.sales.services.billing import BillingService, TakingsByMethod
from app.modules.sales.services.receivables import ReceivablesService


@dataclass(frozen=True, slots=True)
class AppointmentTally:
    """Today's diary, by what has happened to each appointment so far.

    `expected` is the useful number at the start of the day: booked and not
    yet arrived, cancelled or missed — the people reception is still waiting
    for. `no_show` is kept apart from `cancelled` because one is the clinic's
    doing and the other is not.
    """

    total: int
    expected: int
    arrived: int
    completed: int
    cancelled: int
    no_show: int


@dataclass(frozen=True, slots=True)
class OpenDrawer:
    session_id: uuid.UUID
    register_code: str
    business_date: date
    expected: Decimal
    #: An open session dated before today. Somebody forgot to close the till,
    #: and tomorrow's takings are about to land in yesterday's session.
    from_an_earlier_day: bool


@dataclass(frozen=True, slots=True)
class TodayView:
    business_date: date
    currency: str
    #: `None` where the caller may not read that part of the clinic. Absent,
    #: not zero: a receptionist seeing `0 UZS` owed would reasonably conclude
    #: the clinic is owed nothing.
    appointments: AppointmentTally | None
    patients_registered: int | None
    treatments_in_progress: int | None
    treatments_billable: int | None
    payments: TakingsByMethod | None
    debt_outstanding: Decimal | None
    advances_available: Decimal | None
    drawer: OpenDrawer | None
    #: True when the caller may read the drawer at all, which is how the
    #: interface tells "no session open" apart from "not your business".
    drawer_readable: bool
    duplicate_reviews_pending: int | None


class TodayService:
    """Read-only. The audit recorder is here only because this service
    borrows `CashSessionService` to derive the drawer's expected cash, and
    that service takes one; nothing in this file writes an audit row, because
    nothing in this file changes anything."""

    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def view(
        self,
        *,
        organization_id: uuid.UUID,
        business_date: date | None = None,
        currency: str = "UZS",
        include_appointments: bool = False,
        include_patients: bool = False,
        include_clinical: bool = False,
        include_money: bool = False,
        include_drawer: bool = False,
        include_duplicates: bool = False,
    ) -> TodayView:
        today = business_date or clinic_today()

        appointments = (
            await self._appointments(organization_id, today) if include_appointments else None
        )
        patients_registered = (
            await self._patients_registered(organization_id, today) if include_patients else None
        )
        treatments_in_progress = None
        treatments_billable = None
        if include_clinical:
            treatments_in_progress = await self._treatments_in_progress(organization_id)
            treatments_billable = await self._treatments_billable(organization_id)

        payments = None
        debt = None
        advances = None
        if include_money:
            # Every figure here is borrowed from the service that owns its
            # definition, never restated: a second derivation is a second
            # answer waiting to disagree with the first.
            billing = BillingService(self._session, self._audit)
            payments = await billing.takings_on(organization_id=organization_id, on=today)
            advances = await billing.total_unapplied(organization_id=organization_id)
            report = await ReceivablesService(self._session).ageing(
                organization_id=organization_id, as_of=today, currency=currency
            )
            debt = report.total_outstanding

        drawer = await self._drawer(organization_id, today) if include_drawer else None
        duplicates = (
            await self._duplicate_reviews(organization_id) if include_duplicates else None
        )

        return TodayView(
            business_date=today,
            currency=currency,
            appointments=appointments,
            patients_registered=patients_registered,
            treatments_in_progress=treatments_in_progress,
            treatments_billable=treatments_billable,
            payments=payments,
            debt_outstanding=debt,
            advances_available=advances,
            drawer=drawer,
            drawer_readable=include_drawer,
            duplicate_reviews_pending=duplicates,
        )

    # ------------------------------------------------------------- the parts

    async def _appointments(self, organization_id: uuid.UUID, on: date) -> AppointmentTally:
        rows = (
            await self._session.execute(
                select(Appointment.status, func.count())
                .where(
                    Appointment.organization_id == organization_id,
                    Appointment.scheduled_on == on,
                )
                .group_by(Appointment.status)
            )
        ).all()
        by_status = {status: int(count) for status, count in rows}
        return AppointmentTally(
            total=sum(by_status.values()),
            expected=(
                by_status.get(AppointmentStatus.SCHEDULED, 0)
                + by_status.get(AppointmentStatus.CONFIRMED, 0)
            ),
            arrived=by_status.get(AppointmentStatus.ARRIVED, 0),
            completed=by_status.get(AppointmentStatus.COMPLETED, 0),
            cancelled=by_status.get(AppointmentStatus.CANCELLED, 0),
            no_show=by_status.get(AppointmentStatus.NO_SHOW, 0),
        )

    async def _patients_registered(self, organization_id: uuid.UUID, on: date) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count()).where(
                        Patient.organization_id == organization_id,
                        Patient.registered_on == on,
                        # A record folded into another is not a new patient.
                        Patient.merged_into_patient_id.is_(None),
                    )
                )
            ).scalar_one()
        )

    async def _treatments_in_progress(self, organization_id: uuid.UUID) -> int:
        """Work begun or agreed and not yet finished, across every patient.

        Not limited to today: the clinic's register carries the same tooth
        across consecutive visits, so a canal started on Monday is exactly the
        thing somebody needs reminding of on Thursday.
        """
        return int(
            (
                await self._session.execute(
                    select(func.count()).where(
                        TreatmentItem.organization_id == organization_id,
                        TreatmentItem.status.in_(
                            [TreatmentItemStatus.PLANNED, TreatmentItemStatus.IN_PROGRESS]
                        ),
                    )
                )
            ).scalar_one()
        )

    async def _treatments_billable(self, organization_id: uuid.UUID) -> int:
        """Completed work with no invoice line yet — money not asked for.

        `COMPLETED` is the only billable status, and `invoice_line_id` is set
        when the item reaches an invoice, so this counts exactly the work the
        clinic has done and not charged for.
        """
        return int(
            (
                await self._session.execute(
                    select(func.count()).where(
                        TreatmentItem.organization_id == organization_id,
                        TreatmentItem.status == TreatmentItemStatus.COMPLETED,
                        TreatmentItem.invoice_line_id.is_(None),
                    )
                )
            ).scalar_one()
        )

    async def _drawer(self, organization_id: uuid.UUID, today: date) -> OpenDrawer | None:
        summary = await CashSessionService(self._session, self._audit).open_drawer(
            organization_id=organization_id
        )
        if summary is None:
            return None
        return OpenDrawer(
            session_id=summary.session_id,
            register_code=summary.register_code,
            business_date=summary.business_date,
            expected=summary.expected,
            from_an_earlier_day=summary.business_date < today,
        )

    async def _duplicate_reviews(self, organization_id: uuid.UUID) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count()).where(
                        PatientDuplicateReview.organization_id == organization_id,
                        PatientDuplicateReview.status == DuplicateReviewStatus.PENDING,
                    )
                )
            ).scalar_one()
        )
