"""Encounters, treatment items, and turning completed work into a bill.

The boundary this file defends: **completing a treatment is not an
accounting event.** It makes work billable, nothing more. Revenue is
recognised when an invoice is issued, by the sales module, through the
posting policy — and this module never posts anything itself.

That is why `bill_completed_items` builds a *draft* invoice and hands it to
`BillingService`. Issuing it stays a separate, permissioned act, so the
person who records the dentistry is not the person who recognises the
revenue.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import clinic_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.clinic.enums import (
    OPEN_ITEM_STATUSES,
    VALID_TEETH,
    AppointmentStatus,
    EncounterStatus,
    PlanStatus,
    TreatmentItemStatus,
)
from app.modules.clinic.models import (
    Appointment,
    Encounter,
    TreatmentItem,
    TreatmentPlan,
)
from app.modules.clinic.services.patients import PatientService
from app.modules.identity.service import IdentityService
from app.modules.sales.services.billing import BillingService, InvoiceLineInput
from app.modules.sales.services.catalogue import CatalogueService

#: Which plan states may follow which. A plan moves forward through the
#: patient's agreement and the work, or it is abandoned; the two end states
#: are final. Kept as data rather than a chain of `if`s so the API can tell a
#: caller what *is* allowed when it refuses.
PLAN_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.DRAFT: frozenset({PlanStatus.PROPOSED, PlanStatus.ABANDONED}),
    # Back to draft is allowed: a patient asks for a change before agreeing,
    # and re-drafting is honest where editing a proposal in place is not.
    PlanStatus.PROPOSED: frozenset({PlanStatus.ACCEPTED, PlanStatus.DRAFT, PlanStatus.ABANDONED}),
    PlanStatus.ACCEPTED: frozenset({PlanStatus.IN_PROGRESS, PlanStatus.ABANDONED}),
    PlanStatus.IN_PROGRESS: frozenset({PlanStatus.COMPLETED, PlanStatus.ABANDONED}),
    PlanStatus.COMPLETED: frozenset(),
    PlanStatus.ABANDONED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ItemInput:
    """One piece of work to record, on one tooth."""

    service_id: uuid.UUID
    tooth_number: int | None = None
    tooth_surfaces: str | None = None
    finding: str | None = None
    quantity: Decimal = Decimal("1")
    status: TreatmentItemStatus = TreatmentItemStatus.PLANNED
    planned_on: date | None = None


def _validate_tooth(tooth_number: int | None) -> int | None:
    """Reject a tooth that does not exist.

    FDI notation, which is what the clinic already writes in its register.
    A typo such as 19 or 49 names no tooth, and catching it here is cheaper
    than discovering later that a filling was recorded on nothing.
    """
    if tooth_number is None:
        return None
    if tooth_number not in VALID_TEETH:
        raise ValidationFailed(
            f"{tooth_number} is not a tooth number. Use FDI notation: "
            "11-18, 21-28, 31-38, 41-48 for adult teeth, "
            "51-55, 61-65, 71-75, 81-85 for milk teeth.",
            details={"tooth_number": tooth_number},
        )
    return tooth_number


@dataclass(frozen=True, slots=True)
class AppointmentView:
    """A booking with the names a screen has to show, resolved once."""

    appointment: Appointment
    patient_code: str
    patient_name: str
    patient_phone: str | None
    dentist_name: str | None
    #: The visit opened against this booking, where there is one. It is what
    #: turns "the patient is here" into a record of what was done.
    encounter_id: uuid.UUID | None


#: A booking in one of these states no longer claims a slot: the hour it had
#: is free for somebody else.
CLOSED_APPOINTMENT_STATUSES = (
    AppointmentStatus.CANCELLED,
    AppointmentStatus.NO_SHOW,
    AppointmentStatus.COMPLETED,
)


def _minutes(at: time) -> int:
    """Minutes since midnight, which is what two bookings are compared on."""
    return at.hour * 60 + at.minute


class TreatmentService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit
        self._patients = PatientService(session, audit)

    # ----------------------------------------------------------- appointments

    async def book(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        scheduled_on: date,
        scheduled_at: time | None = None,
        dentist_user_id: uuid.UUID | None = None,
        duration_minutes: int | None = None,
        reason: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Book a patient in.

        The date and time are the clinic's own — "Tuesday at three" — and are
        stored as a local date and a local time rather than an instant. The
        clinic has one address and one clock; see `app.core.clock` for why the
        server's own date is not it.

        **One dentist cannot be in two chairs.** Where both appointments have
        a time and a duration, an overlap with a live booking for the same
        dentist is refused. A booking with no time is not a conflict with
        anything: "Tuesday" and "Tuesday at three" are not a double booking,
        and the clinic's register is full of the former.
        """
        patient = await self._patients.get(organization_id=organization_id, patient_id=patient_id)
        if patient.merged_into_patient_id is not None:
            raise Conflict(
                "That record has been merged; book against the surviving one",
                details={"merged_into": str(patient.merged_into_patient_id)},
            )
        dentist = dentist_user_id or patient.assigned_dentist_user_id
        await self._refuse_overlap(
            organization_id=organization_id,
            dentist_user_id=dentist,
            scheduled_on=scheduled_on,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
        )
        appointment = Appointment(
            organization_id=organization_id,
            patient_id=patient_id,
            dentist_user_id=dentist,
            scheduled_on=scheduled_on,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            reason=reason,
            status=AppointmentStatus.SCHEDULED,
        )
        self._session.add(appointment)
        await self._session.flush()

        self._audit.record(
            events.APPOINTMENT_BOOKED,
            organization_id=organization_id,
            target_type="appointment",
            target_id=appointment.id,
            target_label=patient.code,
            actor_user_id=actor_user_id,
            new_values={
                "scheduled_on": scheduled_on.isoformat(),
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                "duration_minutes": duration_minutes,
            },
        )
        return appointment

    async def reschedule(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        scheduled_on: date,
        scheduled_at: time | None = None,
        duration_minutes: int | None = None,
        dentist_user_id: uuid.UUID | None = None,
        reason: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Move a booking. The same appointment, at another time.

        Not a cancellation and a new booking: the patient is the same patient
        keeping the same promise, and the audit record shows where it moved
        from, which a cancel-and-rebook pair does not.

        A closed appointment cannot be moved. Somebody who did not come is a
        fact about a day that has passed; book them again instead.
        """
        appointment = await self.get_appointment(
            organization_id=organization_id, appointment_id=appointment_id
        )
        if appointment.status in CLOSED_APPOINTMENT_STATUSES:
            raise Conflict(
                f"This appointment is {appointment.status.value.lower()} and cannot be "
                "moved. Book a new one.",
                details={"status": appointment.status.value},
            )
        dentist = dentist_user_id or appointment.dentist_user_id
        await self._refuse_overlap(
            organization_id=organization_id,
            dentist_user_id=dentist,
            scheduled_on=scheduled_on,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            ignore_appointment_id=appointment.id,
        )
        before = {
            "scheduled_on": appointment.scheduled_on.isoformat(),
            "scheduled_at": (
                appointment.scheduled_at.isoformat() if appointment.scheduled_at else None
            ),
            "duration_minutes": appointment.duration_minutes,
        }
        appointment.scheduled_on = scheduled_on
        appointment.scheduled_at = scheduled_at
        appointment.duration_minutes = duration_minutes
        appointment.dentist_user_id = dentist
        await self._session.flush()

        self._audit.record(
            events.APPOINTMENT_CHANGED,
            organization_id=organization_id,
            target_type="appointment",
            target_id=appointment.id,
            reason=(reason.strip() if reason and reason.strip() else None),
            actor_user_id=actor_user_id,
            old_values=before,
            new_values={
                "scheduled_on": scheduled_on.isoformat(),
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                "duration_minutes": duration_minutes,
            },
        )
        return appointment

    async def _refuse_overlap(
        self,
        *,
        organization_id: uuid.UUID,
        dentist_user_id: uuid.UUID | None,
        scheduled_on: date,
        scheduled_at: time | None,
        duration_minutes: int | None,
        ignore_appointment_id: uuid.UUID | None = None,
    ) -> None:
        """One dentist, one chair, one patient at a time.

        Only **live** bookings conflict: a cancelled or missed appointment is
        a record of a day, not a claim on it, and the hour it occupied is free
        again. A booking without a time, or without a duration, is not
        compared — the clinic books "Tuesday" as often as "Tuesday at three",
        and treating the former as filling the whole day would stop the day
        being booked at all.

        **The lock is what makes it true.** Two receptionists booking the same
        hour at the same moment would both find the hour free: the check reads
        rows that the other transaction has not committed yet. There is no row
        to lock — the conflict is with a row that does not exist yet — so the
        pair is serialised on the dentist and the day, which is the smallest
        thing that has to be exclusive. It is released when the transaction
        ends, either way.
        """
        if dentist_user_id is None or scheduled_at is None or duration_minutes is None:
            return

        # Serialise bookings for this dentist on this day. `hashtextextended`
        # is stable across sessions, which `hash()` in Python is not.
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"appointment:{organization_id}:{dentist_user_id}:{scheduled_on}", 0
                    )
                )
            )
        )

        start = _minutes(scheduled_at)
        end = start + duration_minutes
        stmt = select(Appointment).where(
            Appointment.organization_id == organization_id,
            Appointment.dentist_user_id == dentist_user_id,
            Appointment.scheduled_on == scheduled_on,
            Appointment.status.notin_(list(CLOSED_APPOINTMENT_STATUSES)),
            Appointment.scheduled_at.isnot(None),
            Appointment.duration_minutes.isnot(None),
        )
        if ignore_appointment_id is not None:
            stmt = stmt.where(Appointment.id != ignore_appointment_id)

        for other in (await self._session.execute(stmt)).scalars():
            assert other.scheduled_at is not None and other.duration_minutes is not None
            other_start = _minutes(other.scheduled_at)
            other_end = other_start + other.duration_minutes
            # Touching is not overlapping: an appointment ending at 15:00 and
            # one starting at 15:00 are back to back, which is how a day is
            # filled.
            if start < other_end and other_start < end:
                raise Conflict(
                    "That dentist is already booked then: "
                    f"{other.scheduled_at.strftime('%H:%M')} for "
                    f"{other.duration_minutes} minutes.",
                    details={
                        "conflicts_with": str(other.id),
                        "scheduled_at": other.scheduled_at.isoformat(),
                        "duration_minutes": other.duration_minutes,
                    },
                )

    async def set_appointment_status(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        status: AppointmentStatus,
        reason: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        appointment = await self.get_appointment(
            organization_id=organization_id, appointment_id=appointment_id
        )
        if appointment.status in CLOSED_APPOINTMENT_STATUSES:
            # `COMPLETED` belongs here too: a patient who has been seen cannot
            # later become a no-show. Leaving it out let a finished visit be
            # cancelled, which would free a slot in the past and lose the fact
            # that somebody was treated in it.
            raise Conflict(
                f"This appointment is {appointment.status.value.lower()} and is closed",
                details={"status": appointment.status.value},
            )
        if status is AppointmentStatus.CANCELLED and not (reason and reason.strip()):
            raise ValidationFailed("Cancelling an appointment requires a reason")

        before = appointment.status.value
        appointment.status = status
        if status is AppointmentStatus.CANCELLED:
            appointment.cancelled_reason = (reason or "").strip()
        await self._session.flush()

        self._audit.record(
            events.APPOINTMENT_CHANGED,
            organization_id=organization_id,
            target_type="appointment",
            target_id=appointment.id,
            reason=(reason or None),
            actor_user_id=actor_user_id,
            old_values={"status": before},
            new_values={"status": status.value},
        )
        return appointment

    # ------------------------------------------------------------- encounters

    async def open_encounter(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        occurred_on: date | None = None,
        appointment_id: uuid.UUID | None = None,
        dentist_user_id: uuid.UUID | None = None,
        complaint: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Encounter:
        """Begin a visit. A walk-in has no appointment, which is allowed."""
        patient = await self._patients.get(organization_id=organization_id, patient_id=patient_id)
        if patient.merged_into_patient_id is not None:
            raise Conflict("That record has been merged; use the surviving one")

        if appointment_id is not None:
            appointment = await self.get_appointment(
                organization_id=organization_id, appointment_id=appointment_id
            )
            if appointment.patient_id != patient_id:
                raise ValidationFailed("That appointment belongs to a different patient")
            appointment.status = AppointmentStatus.ARRIVED

        encounter = Encounter(
            organization_id=organization_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            dentist_user_id=dentist_user_id or patient.assigned_dentist_user_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="encounter",
                prefix="ENC-",
            ),
            occurred_on=occurred_on or clinic_today(),
            status=EncounterStatus.OPEN,
            complaint=complaint,
        )
        self._session.add(encounter)
        await self._session.flush()

        self._audit.record(
            events.ENCOUNTER_OPENED,
            organization_id=organization_id,
            target_type="encounter",
            target_id=encounter.id,
            target_label=encounter.code,
            business_event=f"encounter:{encounter.id}",
            actor_user_id=actor_user_id,
            new_values={"patient_code": patient.code, "occurred_on": encounter.occurred_on},
        )
        return encounter

    async def complete_encounter(
        self,
        *,
        organization_id: uuid.UUID,
        encounter_id: uuid.UUID,
        clinical_notes: str | None = None,
        body_weight_kg: Decimal | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Encounter:
        """End the visit.

        Completing a visit bills nothing and posts nothing. Plenty of visits
        end with no charge at all — the clinic's own register is full of
        consultations and reviews with no amount beside them.
        """
        encounter = await self.get_encounter(
            organization_id=organization_id, encounter_id=encounter_id
        )
        if encounter.status is not EncounterStatus.OPEN:
            raise Conflict(
                f"This visit is already {encounter.status.value.lower()}",
                details={"status": encounter.status.value},
            )
        if clinical_notes is not None:
            encounter.clinical_notes = clinical_notes
        if body_weight_kg is not None:
            encounter.body_weight_kg = body_weight_kg
        encounter.status = EncounterStatus.COMPLETED
        encounter.completed_at = datetime.now(UTC)

        if encounter.appointment_id is not None:
            appointment = await self.get_appointment(
                organization_id=organization_id, appointment_id=encounter.appointment_id
            )
            if appointment.status is AppointmentStatus.ARRIVED:
                appointment.status = AppointmentStatus.COMPLETED
        await self._session.flush()

        self._audit.record(
            events.ENCOUNTER_COMPLETED,
            organization_id=organization_id,
            target_type="encounter",
            target_id=encounter.id,
            target_label=encounter.code,
            business_event=f"encounter:{encounter.id}",
            actor_user_id=actor_user_id,
            new_values={"status": encounter.status.value},
        )
        return encounter

    # --------------------------------------------------------- treatment items

    async def record_items(
        self,
        *,
        organization_id: uuid.UUID,
        encounter_id: uuid.UUID,
        items: list[ItemInput],
        plan_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> list[TreatmentItem]:
        """Record work against a visit, one row per tooth.

        Per tooth rather than per visit because that is what dentistry is:
        the clinic's register routinely shows three or four teeth in one
        sitting, each with its own finding and its own fate.
        """
        if not items:
            raise ValidationFailed("Record at least one treatment item")

        encounter = await self.get_encounter(
            organization_id=organization_id, encounter_id=encounter_id
        )
        if encounter.status is not EncounterStatus.OPEN:
            raise Conflict(
                "This visit is closed. Open a new one to record further work.",
                details={"status": encounter.status.value},
            )

        catalogue = CatalogueService(self._session, self._audit)
        recorded: list[TreatmentItem] = []
        for item in items:
            await catalogue.get_service(organization_id=organization_id, service_id=item.service_id)
            if item.quantity <= 0:
                raise ValidationFailed("A treatment quantity must be positive")
            row = TreatmentItem(
                organization_id=organization_id,
                patient_id=encounter.patient_id,
                encounter_id=encounter.id,
                plan_id=plan_id,
                service_id=item.service_id,
                dentist_user_id=encounter.dentist_user_id,
                tooth_number=_validate_tooth(item.tooth_number),
                tooth_surfaces=item.tooth_surfaces,
                finding=item.finding,
                status=item.status,
                quantity=item.quantity,
                planned_on=item.planned_on or encounter.occurred_on,
                completed_on=(
                    encounter.occurred_on if item.status is TreatmentItemStatus.COMPLETED else None
                ),
                completed_by_user_id=(
                    actor_user_id if item.status is TreatmentItemStatus.COMPLETED else None
                ),
            )
            self._session.add(row)
            recorded.append(row)
        await self._session.flush()

        self._audit.record(
            events.TREATMENT_RECORDED,
            organization_id=organization_id,
            target_type="encounter",
            target_id=encounter.id,
            target_label=encounter.code,
            business_event=f"encounter:{encounter.id}",
            actor_user_id=actor_user_id,
            new_values={
                "items": len(recorded),
                "teeth": [r.tooth_number for r in recorded if r.tooth_number],
            },
        )
        return recorded

    async def complete_item(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        completed_on: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> TreatmentItem:
        """Mark one piece of work finished, which makes it billable.

        Still not an accounting event. Nothing is posted here.
        """
        item = await self.get_item(organization_id=organization_id, item_id=item_id)
        if item.status is TreatmentItemStatus.COMPLETED:
            raise Conflict("That treatment is already completed")
        if item.status not in OPEN_ITEM_STATUSES:
            raise Conflict(
                f"A {item.status.value.lower()} treatment cannot be completed",
                details={"status": item.status.value},
            )
        item.status = TreatmentItemStatus.COMPLETED
        item.completed_on = completed_on or clinic_today()
        item.completed_by_user_id = actor_user_id
        await self._session.flush()

        self._audit.record(
            events.TREATMENT_COMPLETED,
            organization_id=organization_id,
            target_type="treatment_item",
            target_id=item.id,
            actor_user_id=actor_user_id,
            new_values={
                "tooth_number": item.tooth_number,
                "completed_on": item.completed_on,
            },
        )
        return item

    async def cancel_item(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> TreatmentItem:
        if not reason or not reason.strip():
            raise ValidationFailed("Cancelling a treatment requires a reason")
        item = await self.get_item(organization_id=organization_id, item_id=item_id)
        if item.invoice_line_id is not None:
            raise Conflict(
                "That treatment has been billed. Correct the invoice instead.",
                details={"invoice_line_id": str(item.invoice_line_id)},
            )
        if item.status is TreatmentItemStatus.CANCELLED:
            raise Conflict("That treatment is already cancelled")
        item.status = TreatmentItemStatus.CANCELLED
        item.cancelled_reason = reason.strip()
        await self._session.flush()
        return item

    async def correct_item(
        self,
        *,
        organization_id: uuid.UUID,
        item_id: uuid.UUID,
        replacement: ItemInput,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> TreatmentItem:
        """Supersede a recorded treatment with what really happened.

        The original stays and is marked `CORRECTED`, pointing at its
        replacement. A clinical record that can be edited in place is not a
        clinical record.
        """
        if not reason or not reason.strip():
            raise ValidationFailed("A correction requires a reason")
        original = await self.get_item(organization_id=organization_id, item_id=item_id)
        if original.status is TreatmentItemStatus.CORRECTED:
            raise Conflict("That treatment has already been corrected")
        if original.invoice_line_id is not None:
            raise Conflict(
                "That treatment has been billed. Correct the invoice first.",
                details={"invoice_line_id": str(original.invoice_line_id)},
            )

        new_item = TreatmentItem(
            organization_id=organization_id,
            patient_id=original.patient_id,
            encounter_id=original.encounter_id,
            plan_id=original.plan_id,
            service_id=replacement.service_id,
            dentist_user_id=original.dentist_user_id,
            tooth_number=_validate_tooth(replacement.tooth_number),
            tooth_surfaces=replacement.tooth_surfaces,
            finding=replacement.finding,
            status=replacement.status,
            quantity=replacement.quantity,
            planned_on=original.planned_on,
            completed_on=(
                clinic_today() if replacement.status is TreatmentItemStatus.COMPLETED else None
            ),
            completed_by_user_id=(
                actor_user_id if replacement.status is TreatmentItemStatus.COMPLETED else None
            ),
        )
        self._session.add(new_item)
        await self._session.flush()

        original.status = TreatmentItemStatus.CORRECTED
        original.corrected_by_item_id = new_item.id
        original.cancelled_reason = reason.strip()
        await self._session.flush()

        self._audit.record(
            events.TREATMENT_CORRECTED,
            organization_id=organization_id,
            target_type="treatment_item",
            target_id=original.id,
            reason=reason.strip(),
            actor_user_id=actor_user_id,
            old_values={"tooth_number": original.tooth_number},
            new_values={"tooth_number": new_item.tooth_number, "replacement": str(new_item.id)},
        )
        return new_item

    # ------------------------------------------------------------ plans

    async def create_plan(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        title: str,
        dentist_user_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> TreatmentPlan:
        await self._patients.get(organization_id=organization_id, patient_id=patient_id)
        plan = TreatmentPlan(
            organization_id=organization_id,
            patient_id=patient_id,
            dentist_user_id=dentist_user_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="treatment_plan",
                prefix="TP-",
            ),
            title=title,
            status=PlanStatus.DRAFT,
            notes=notes,
        )
        self._session.add(plan)
        await self._session.flush()

        self._audit.record(
            events.PLAN_CREATED,
            organization_id=organization_id,
            target_type="treatment_plan",
            target_id=plan.id,
            target_label=plan.code,
            business_event=f"treatment_plan:{plan.id}",
            new_values={"title": plan.title, "patient_id": str(patient_id)},
        )
        return plan

    async def list_plans(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
        statuses: list[PlanStatus] | None = None,
    ) -> list[TreatmentPlan]:
        stmt = select(TreatmentPlan).where(TreatmentPlan.organization_id == organization_id)
        if patient_id is not None:
            stmt = stmt.where(TreatmentPlan.patient_id == patient_id)
        if statuses:
            stmt = stmt.where(TreatmentPlan.status.in_(statuses))
        stmt = stmt.order_by(TreatmentPlan.created_at.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_plan(self, *, organization_id: uuid.UUID, plan_id: uuid.UUID) -> TreatmentPlan:
        stmt = select(TreatmentPlan).where(
            TreatmentPlan.organization_id == organization_id,
            TreatmentPlan.id == plan_id,
        )
        plan = (await self._session.execute(stmt)).scalar_one_or_none()
        if plan is None:
            raise NotFound("Treatment plan not found")
        return plan

    async def plan_items(
        self, *, organization_id: uuid.UUID, plan_id: uuid.UUID
    ) -> list[TreatmentItem]:
        stmt = (
            select(TreatmentItem)
            .where(
                TreatmentItem.organization_id == organization_id,
                TreatmentItem.plan_id == plan_id,
            )
            .order_by(TreatmentItem.planned_on, TreatmentItem.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def set_plan_status(
        self,
        *,
        organization_id: uuid.UUID,
        plan_id: uuid.UUID,
        status: PlanStatus,
        actor_user_id: uuid.UUID | None = None,
    ) -> TreatmentPlan:
        """Move a course of treatment to its next state.

        The transitions are narrow on purpose. A plan is a clinical
        commitment the patient agreed to, so it moves forward through
        agreement and work, or it is abandoned — it never jumps from a draft
        straight to finished, and a finished or abandoned plan is final.

        Completing a plan while work on it is still open is refused rather
        than silently cancelling that work: the dentist decides what happens
        to an unfinished tooth, not the plan screen.
        """
        plan = await self.get_plan(organization_id=organization_id, plan_id=plan_id)
        if plan.status is status:
            return plan
        allowed = PLAN_TRANSITIONS.get(plan.status, frozenset())
        if status not in allowed:
            raise Conflict(
                f"A {plan.status.value.lower().replace('_', ' ')} plan cannot become "
                f"{status.value.lower().replace('_', ' ')}",
                details={
                    "status": plan.status.value,
                    "allowed": sorted(s.value for s in allowed),
                },
            )

        if status is PlanStatus.COMPLETED:
            open_items = [
                item
                for item in await self.plan_items(organization_id=organization_id, plan_id=plan_id)
                if item.status in OPEN_ITEM_STATUSES
            ]
            if open_items:
                raise Conflict(
                    "This plan still has unfinished work. Complete or cancel it first.",
                    details={"open_items": len(open_items)},
                )

        previous = plan.status
        plan.status = status
        if status is PlanStatus.IN_PROGRESS and plan.started_on is None:
            plan.started_on = clinic_today()
        if status is PlanStatus.COMPLETED:
            plan.completed_on = clinic_today()
        await self._session.flush()

        self._audit.record(
            events.PLAN_STATUS_CHANGED,
            organization_id=organization_id,
            target_type="treatment_plan",
            target_id=plan.id,
            target_label=plan.code,
            business_event=f"treatment_plan:{plan.id}",
            actor_user_id=actor_user_id,
            old_values={"status": previous.value},
            new_values={"status": status.value},
        )
        return plan

    # --------------------------------------------------------------- billing

    async def billable_items(
        self, *, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> list[TreatmentItem]:
        """Completed work that has not been billed yet."""
        stmt = (
            select(TreatmentItem)
            .where(
                TreatmentItem.organization_id == organization_id,
                TreatmentItem.patient_id == patient_id,
                TreatmentItem.status == TreatmentItemStatus.COMPLETED,
                TreatmentItem.invoice_line_id.is_(None),
            )
            .order_by(TreatmentItem.completed_on, TreatmentItem.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def bill_completed_items(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        item_ids: list[uuid.UUID] | None = None,
        issue_date: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ):
        """Draft an invoice from completed work.

        A **draft**, deliberately. Issuing it is what recognises revenue, and
        that stays a separate act with its own permission — the dentist who
        did the work does not thereby decide that the clinic has earned it.

        Items from several visits can go on one invoice, which is how the
        clinic already behaves: a patient coming back for a second and third
        appointment settles once at the end.
        """
        patient = await self._patients.get(organization_id=organization_id, patient_id=patient_id)
        if patient.customer_id is None:
            raise Conflict(
                "This patient has no billing record, so nothing can be invoiced to them",
                details={"patient": patient.code},
            )

        available = await self.billable_items(
            organization_id=organization_id, patient_id=patient_id
        )
        if item_ids is not None:
            wanted = set(item_ids)
            chosen = [i for i in available if i.id in wanted]
            missing = wanted - {i.id for i in chosen}
            if missing:
                raise ValidationFailed(
                    "Some of those treatments are not completed, or are already billed",
                    details={"items": sorted(str(m) for m in missing)},
                )
        else:
            chosen = available
        if not chosen:
            raise Conflict("There is no completed, unbilled treatment for this patient")

        catalogue = CatalogueService(self._session, self._audit)
        billing = BillingService(self._session, self._audit)
        on = issue_date or clinic_today()

        lines: list[InvoiceLineInput] = []
        for item in chosen:
            service = await catalogue.get_service(
                organization_id=organization_id, service_id=item.service_id
            )
            # The description names the tooth, which is what a patient
            # recognises on a bill. The *finding* stays in the clinical
            # record: an invoice is read by people without clinical access.
            description = service.name
            if item.tooth_number is not None:
                description = f"{service.name} (tooth {item.tooth_number})"
            lines.append(
                InvoiceLineInput(
                    description=description,
                    service_id=item.service_id,
                    quantity=item.quantity,
                )
            )

        invoice = await billing.create_invoice(
            organization_id=organization_id,
            customer_id=patient.customer_id,
            issue_date=on,
            lines=lines,
        )
        invoice_lines = await billing.list_lines(
            organization_id=organization_id, invoice_id=invoice.id
        )
        billed_at = datetime.now(UTC)
        for item, line in zip(chosen, invoice_lines, strict=True):
            item.invoice_line_id = line.id
            item.unit_price = line.unit_price
            item.billed_at = billed_at
        await self._session.flush()

        self._audit.record(
            events.TREATMENT_BILLED,
            organization_id=organization_id,
            target_type="invoice",
            target_id=invoice.id,
            target_label=invoice.number,
            business_event=f"invoice:{invoice.id}",
            actor_user_id=actor_user_id,
            new_values={
                "patient_code": patient.code,
                "items": len(chosen),
                "total": invoice.total,
            },
        )
        return invoice

    # ------------------------------------------------------------------ reads

    async def get_appointment(
        self, *, organization_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> Appointment:
        row = (
            await self._session.execute(
                select(Appointment).where(
                    Appointment.organization_id == organization_id,
                    Appointment.id == appointment_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("Appointment not found")
        return row

    async def get_encounter(
        self, *, organization_id: uuid.UUID, encounter_id: uuid.UUID
    ) -> Encounter:
        row = (
            await self._session.execute(
                select(Encounter).where(
                    Encounter.organization_id == organization_id,
                    Encounter.id == encounter_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("Visit not found")
        return row

    async def get_item(self, *, organization_id: uuid.UUID, item_id: uuid.UUID) -> TreatmentItem:
        row = (
            await self._session.execute(
                select(TreatmentItem).where(
                    TreatmentItem.organization_id == organization_id,
                    TreatmentItem.id == item_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("Treatment item not found")
        return row

    async def list_encounters(
        self, *, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> list[Encounter]:
        stmt = (
            select(Encounter)
            .where(
                Encounter.organization_id == organization_id,
                Encounter.patient_id == patient_id,
            )
            .order_by(Encounter.occurred_on.desc(), Encounter.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_items(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
        encounter_id: uuid.UUID | None = None,
    ) -> list[TreatmentItem]:
        stmt = select(TreatmentItem).where(TreatmentItem.organization_id == organization_id)
        if patient_id is not None:
            stmt = stmt.where(TreatmentItem.patient_id == patient_id)
        if encounter_id is not None:
            stmt = stmt.where(TreatmentItem.encounter_id == encounter_id)
        stmt = stmt.order_by(TreatmentItem.created_at)
        return list((await self._session.execute(stmt)).scalars().all())

    async def appointments_for_the_day(
        self,
        *,
        organization_id: uuid.UUID,
        on: date | None = None,
        patient_id: uuid.UUID | None = None,
    ) -> list[AppointmentView]:
        """The day's bookings, with the names the screen needs.

        The names are resolved here rather than in the browser: an
        appointments list with patient ids in it is not a list anybody can
        read, and fetching each patient from the screen would be a request
        per row. There are at most a handful of dentists in this clinic, so
        each is looked up once and reused.
        """
        rows = await self.list_appointments(
            organization_id=organization_id, on=on, patient_id=patient_id
        )
        identity = IdentityService(self._session, self._audit)
        dentists: dict[uuid.UUID, str] = {}
        views: list[AppointmentView] = []
        for appointment in rows:
            patient = await self._patients.get(
                organization_id=organization_id, patient_id=appointment.patient_id
            )
            dentist_name: str | None = None
            if appointment.dentist_user_id is not None:
                if appointment.dentist_user_id not in dentists:
                    try:
                        user = await identity.get_user(appointment.dentist_user_id)
                        dentists[appointment.dentist_user_id] = user.full_name
                    except NotFound:
                        dentists[appointment.dentist_user_id] = ""
                dentist_name = dentists[appointment.dentist_user_id] or None
            encounter = (
                (
                    await self._session.execute(
                        select(Encounter.id).where(
                            Encounter.organization_id == organization_id,
                            Encounter.appointment_id == appointment.id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            views.append(
                AppointmentView(
                    appointment=appointment,
                    patient_code=patient.code,
                    patient_name=patient.display_name,
                    patient_phone=patient.phone,
                    dentist_name=dentist_name,
                    encounter_id=encounter,
                )
            )
        views.sort(
            key=lambda v: (
                v.appointment.scheduled_on,
                v.appointment.scheduled_at or time(0, 0),
                v.patient_name,
            )
        )
        return views

    async def list_appointments(
        self,
        *,
        organization_id: uuid.UUID,
        on: date | None = None,
        patient_id: uuid.UUID | None = None,
    ) -> list[Appointment]:
        stmt = select(Appointment).where(Appointment.organization_id == organization_id)
        if on is not None:
            stmt = stmt.where(Appointment.scheduled_on == on)
        if patient_id is not None:
            stmt = stmt.where(Appointment.patient_id == patient_id)
        stmt = stmt.order_by(Appointment.scheduled_on, Appointment.created_at)
        return list((await self._session.execute(stmt)).scalars().all())

    async def bookable_dentists(self, *, organization_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        """The people a booking may be given to, by id and name.

        Reception books the day, and so needs to know who is in the chairs —
        which is not the same as being allowed to read the staff directory.
        So the appointment screen asks for this list, gated on
        `clinic.appointment.read`, instead of listing users: it carries a
        name and an id and nothing else, and both already appear in the day
        it is drawn beside.

        Who holds the `dentist` role is identity's question, not this
        module's, so it is asked rather than queried. The owner counts too:
        in a clinic of three the owner is usually also the dentist.
        """
        identity = IdentityService(self._session, self._audit)
        people = await identity.active_members_with_role(
            organization_id=organization_id,
            role_code="dentist",
            include_primary_owner=True,
        )
        return [(user.id, user.full_name) for user in people]
