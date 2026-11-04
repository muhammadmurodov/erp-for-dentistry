"""Clinic endpoints: patients, the schedule, the clinical record, settlement.

The permissions on these routes are the access-control design, not decoration.
Three groups, deliberately disjoint:

* `clinic.patient.*` and `clinic.appointment.*` — reception. Names, telephone
  numbers, the day's schedule.
* `clinic.clinical.*` — the dentist. Complaints, findings, notes, teeth.
  Reception does not hold it, so no reception screen can return a diagnosis.
* `sales.*` — billing. Drafting an invoice, taking money. The dentist who
  completes the work does not thereby recognise the revenue.

The settlement routes sit here rather than under `/sales` because settling a
patient's bill touches a receivable *and* a cash drawer in one transaction,
and only this module is above both.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AuditDep, DbSession, OrgCtx, require_permissions
from app.core.money import ZERO, quantize
from app.modules.audit import events
from app.modules.clinic.enums import OPEN_ITEM_STATUSES, PlanStatus, SettlementStatus
from app.modules.clinic.schemas import (
    AppointmentCreateRequest,
    AppointmentRescheduleRequest,
    AppointmentResponse,
    AppointmentRow,
    AppointmentStatusRequest,
    BillItemsRequest,
    CompleteItemRequest,
    CorrectItemRequest,
    DentistRow,
    DuplicateCandidateResponse,
    DuplicateReviewDetail,
    DuplicateReviewResponse,
    EncounterCompleteRequest,
    EncounterOpenRequest,
    EncounterResponse,
    MergeRequest,
    PatientBalance,
    PatientDetail,
    PatientInvoiceRow,
    PatientRegistered,
    PatientRegisterRequest,
    PatientSummary,
    PatientUpdateRequest,
    PlanCreateRequest,
    PlanStatusRequest,
    ReasonRequest,
    RecordItemsRequest,
    ResolveReviewRequest,
    SettlementHistoryRow,
    SettlementRequest,
    SettlementResult,
    SettlementView,
    TodayResponse,
    TreatmentItemResponse,
    TreatmentMaterialRequest,
    TreatmentPlanDetail,
    TreatmentPlanResponse,
)
from app.modules.clinic.services.patients import PatientService
from app.modules.clinic.services.settlement import SettlementService
from app.modules.clinic.services.today import TodayService
from app.modules.clinic.services.treatments import ItemInput, TreatmentService
from app.modules.inventory.enums import StockMovementKind
from app.modules.inventory.schemas import MovementResponse
from app.modules.inventory.services.stock import StockService
from app.modules.sales.enums import InvoiceStatus
from app.modules.sales.schemas import InvoiceResponse
from app.modules.sales.services.billing import BillingService

router = APIRouter(prefix="/clinic", tags=["clinic"])


async def _record_clinical_read(
    *,
    db: DbSession,
    audit: AuditDep,
    context: OrgCtx,
    target_type: str,
    target_id: uuid.UUID,
    target_label: str | None = None,
) -> None:
    """Write the audit row for a read of somebody's clinical record.

    `SECURITY.md` states that `clinic.clinical.read` is sensitive and that
    such reads are audited. Until this existed the permission was enforced
    but the read itself left no trace, so the document described a control
    the system did not have. Who looked at a patient's treatment, and when,
    is exactly the question a privacy complaint asks.

    A read commits, which a GET otherwise would not: the audit row is the
    only thing written, and it has to survive the response.
    """
    audit.record(
        events.CLINICAL_RECORD_READ,
        organization_id=context.require_organization(),
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        actor_user_id=context.user_id,
    )
    await db.commit()


def _item_input(payload) -> ItemInput:
    return ItemInput(
        service_id=payload.service_id,
        tooth_number=payload.tooth_number,
        tooth_surfaces=payload.tooth_surfaces,
        finding=payload.finding,
        quantity=payload.quantity,
        status=payload.status,
        planned_on=payload.planned_on,
    )


# ---------------------------------------------------------------- patients


@router.get(
    "/patients",
    response_model=list[PatientSummary],
    dependencies=[Depends(require_permissions("clinic.patient.read"))],
)
async def search_patients(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    query: str | None = Query(default=None, max_length=128),
    include_merged: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PatientSummary]:
    """Find a patient by name, code or telephone number."""
    patients = await PatientService(db, audit).search(
        organization_id=context.require_organization(),
        query=query,
        include_merged=include_merged,
        limit=limit,
    )
    return [PatientSummary.model_validate(p) for p in patients]


@router.post(
    "/patients",
    response_model=PatientRegistered,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("clinic.patient.manage"))],
)
async def register_patient(
    payload: PatientRegisterRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PatientRegistered:
    """Register a patient, reporting anyone they might already be.

    Never refused on a suspected duplicate. The candidates come back with the
    new record and a review is queued for somebody to settle, because a
    receptionist with a patient in the chair has to be able to finish.
    """
    organization_id = context.require_organization()
    patient, candidates = await PatientService(db, audit).register(
        organization_id=organization_id,
        family_name=payload.family_name,
        given_name=payload.given_name,
        patronymic=payload.patronymic,
        birth_year=payload.birth_year,
        birth_date=payload.birth_date,
        phone=payload.phone,
        address=payload.address,
        notes=payload.notes,
        assigned_dentist_user_id=payload.assigned_dentist_user_id,
        registered_on=payload.registered_on,
        allow_duplicate=payload.allow_duplicate,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return PatientRegistered(
        patient=PatientSummary.model_validate(patient),
        duplicate_candidates=[
            DuplicateCandidateResponse(
                patient_id=c.patient_id,
                code=c.code,
                display_name=c.display_name,
                birth_year=c.birth_year,
                phone=c.phone,
                reason=c.reason,
            )
            for c in candidates
        ],
    )


@router.get(
    "/patients/{patient_id}",
    response_model=PatientDetail,
    dependencies=[Depends(require_permissions("clinic.patient.read"))],
)
async def patient_detail(
    patient_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PatientDetail:
    """The patient page: who they are, what they owe, which bills are open.

    The debt comes from the sales service. Nothing on this page is a stored
    balance, which is why no screen can show a figure the ledger disagrees
    with.

    **The money half needs `sales.invoice.read` and is omitted without it.**
    The page used to require both permissions, which refused the dentist —
    who may read a patient and record treatment, but not read invoices — and
    so put the entire clinical workflow out of reach of a browser. Filtering
    the response grants nobody anything: a caller without the sales
    permission simply gets `balance: null` and no invoice rows, exactly as
    the dashboard withholds what it may not show (ADR-044).
    """
    organization_id = context.require_organization()
    patients = PatientService(db, audit)
    treatments = TreatmentService(db, audit)
    billing = BillingService(db, audit)
    settlement = SettlementService(db, audit)

    patient = await patients.get(organization_id=organization_id, patient_id=patient_id)

    # Withheld rather than zeroed: a dentist shown `0 UZS outstanding` would
    # reasonably tell the patient they owe nothing.
    may_read_money = context.has("sales.invoice.read")
    rows: list[PatientInvoiceRow] | None = None
    balance: PatientBalance | None = None
    if may_read_money:
        rows = []
        balance = PatientBalance(
            customer_id=None,
            invoiced=ZERO,
            paid=ZERO,
            outstanding=ZERO,
            advances_available=ZERO,
            written_off=ZERO,
        )
    if may_read_money and patient.customer_id is not None:
        figures = await billing.customer_balance(
            organization_id=organization_id, customer_id=patient.customer_id
        )
        balance = PatientBalance(
            customer_id=patient.customer_id,
            invoiced=figures.invoiced,
            paid=figures.paid,
            outstanding=figures.outstanding,
            advances_available=max(ZERO, figures.unapplied_credit),
            written_off=figures.written_off,
        )
        for invoice in await billing.list_invoices(
            organization_id=organization_id, customer_id=patient.customer_id
        ):
            # A draft is answered here rather than viewed: `view()` would be
            # a second round of queries to report a bill that carries no
            # receivable at all. Its remaining balance is zero for the same
            # reason a voided invoice's is — nothing is owed until it is
            # issued.
            if invoice.status is InvoiceStatus.DRAFT:
                paid, remaining = ZERO, ZERO
                state = SettlementStatus.NOT_PAYABLE
            else:
                view = await settlement.view(organization_id=organization_id, invoice_id=invoice.id)
                paid, remaining, state = view.paid, view.remaining, view.status
            rows.append(
                PatientInvoiceRow(
                    invoice_id=invoice.id,
                    number=invoice.number,
                    issue_date=invoice.issue_date,
                    status=invoice.status,
                    settlement_status=state,
                    total=quantize(invoice.total, invoice.currency),
                    paid=paid,
                    remaining=remaining,
                    currency=invoice.currency,
                )
            )

    billable = await treatments.billable_items(
        organization_id=organization_id, patient_id=patient_id
    )
    reviews = await patients.list_reviews(organization_id=organization_id)
    return PatientDetail(
        patient=PatientSummary.model_validate(patient),
        balance=balance,
        invoices=rows,
        billable_items=len(billable),
        open_duplicate_reviews=sum(
            1 for r in reviews if patient_id in (r.patient_id, r.candidate_patient_id)
        ),
    )


@router.patch(
    "/patients/{patient_id}",
    response_model=PatientSummary,
    dependencies=[Depends(require_permissions("clinic.patient.manage"))],
)
async def update_patient(
    patient_id: uuid.UUID,
    payload: PatientUpdateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PatientSummary:
    patient = await PatientService(db, audit).update(
        organization_id=context.require_organization(),
        patient_id=patient_id,
        actor_user_id=context.user_id,
        **payload.model_dump(exclude_unset=True),
    )
    await db.commit()
    return PatientSummary.model_validate(patient)


@router.post(
    "/patients/{patient_id}/merge",
    response_model=PatientSummary,
    dependencies=[Depends(require_permissions("clinic.patient.manage"))],
)
async def merge_patient(
    patient_id: uuid.UUID,
    payload: MergeRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> PatientSummary:
    """Declare that two records are one person. Reason mandatory, audited.

    The losing record is kept and marked. Treatments were recorded against
    it, and a trail that cannot say where they went is not a trail.
    """
    patient = await PatientService(db, audit).merge(
        organization_id=context.require_organization(),
        patient_id=patient_id,
        into_patient_id=payload.into_patient_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return PatientSummary.model_validate(patient)


@router.get(
    "/today",
    response_model=TodayResponse,
)
async def today(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    on: date | None = Query(default=None, description="Defaults to today"),
) -> TodayResponse:
    """What is happening in the clinic today.

    Every figure is derived on the server from the records themselves — the
    outstanding debt through `ReceivablesService`, which is the one definition
    of what a patient owes, and the drawer's expected cash through
    `CashSessionService`, which derives it from the movements. Nothing here is
    a stored total, and nothing is summed in the browser from a truncated
    list.

    **It returns only what this caller may already read.** Each section is
    gated on the permission that guards the screen it summarises, and a
    section the caller may not see is not queried at all, so it comes back
    `null` rather than zero. A dashboard that aggregates its way around a
    permission is a permission bug with a chart on it.

    The endpoint itself carries no module gate, so everybody gets a dashboard
    — the accountant, who may not read a patient, gets the financial half and
    nothing clinical; the dentist gets the clinical half and no figures. An
    organization member with none of these permissions gets a page of nulls,
    which is the honest answer rather than a 403 on the home screen.
    """
    organization_id = context.require_organization()
    view = await TodayService(db, audit).view(
        organization_id=organization_id,
        business_date=on,
        include_appointments=context.has("clinic.appointment.read"),
        include_patients=context.has("clinic.patient.read"),
        # Counts of clinical work are clinical signal, so they follow the
        # clinical permission rather than the patient one (OPEN_QUESTIONS C1).
        include_clinical=context.has("clinic.clinical.read"),
        # `reporting.financial.read`, not `sales.invoice.read`: these are the
        # same clinic-wide aggregates the ageing report serves, and that
        # report is gated on the reporting permission. A receptionist may read
        # an individual invoice and may not read the total owed, and a summary
        # screen is not a way around that.
        include_money=context.has("reporting.financial.read"),
        include_drawer=context.has("cash.register.read"),
        include_duplicates=context.has("clinic.patient.manage"),
    )
    return TodayResponse.model_validate(view)


@router.get(
    "/duplicate-reviews",
    response_model=list[DuplicateReviewDetail],
    dependencies=[Depends(require_permissions("clinic.patient.manage"))],
)
async def list_duplicate_reviews(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[DuplicateReviewDetail]:
    """The queue of suspected duplicates, both records attached.

    Nothing here has been merged. Registration never refuses a patient on a
    suspicion — the receptionist has the person in front of them — so a row in
    this queue means two records look alike and nobody has decided yet. The
    decision needs the two side by side, which is why the records travel with
    the review instead of as ids to go and fetch.
    """
    queue = await PatientService(db, audit).review_queue(
        organization_id=context.require_organization()
    )
    return [
        DuplicateReviewDetail(
            id=review.id,
            reason=review.reason,
            status=review.status,
            created_at=review.created_at,
            decided_at=review.decided_at,
            decision_note=review.decision_note,
            patient=PatientSummary.model_validate(patient),
            candidate=PatientSummary.model_validate(candidate),
            # `resolve_review` folds the patient into the candidate: the record
            # already on file is the one that keeps its history.
            survivor_id=candidate.id,
        )
        for review, patient, candidate in queue
    ]


@router.post(
    "/duplicate-reviews/{review_id}/resolve",
    response_model=DuplicateReviewResponse,
    dependencies=[Depends(require_permissions("clinic.patient.manage"))],
)
async def resolve_duplicate_review(
    review_id: uuid.UUID,
    payload: ResolveReviewRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> DuplicateReviewResponse:
    """Settle a suspected duplicate, one way or the other.

    Recording two records as distinct matters as much as merging them: it is
    what stops the same pair being raised again every visit.
    """
    review = await PatientService(db, audit).resolve_review(
        organization_id=context.require_organization(),
        review_id=review_id,
        merge=payload.merge,
        note=payload.note,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return DuplicateReviewResponse.model_validate(review)


# ------------------------------------------------------------ appointments


@router.get(
    "/appointments",
    response_model=list[AppointmentRow],
    dependencies=[Depends(require_permissions("clinic.appointment.read"))],
)
async def list_appointments(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    on: date | None = None,
    patient_id: uuid.UUID | None = None,
) -> list[AppointmentRow]:
    """The day's bookings, or one patient's history of them.

    `on` defaults to nothing rather than to today: asking for a patient's
    appointments means all of them, and the screen that wants a day says
    which day. The day it means is the clinic's (`app.core.clock`).
    """
    views = await TreatmentService(db, audit).appointments_for_the_day(
        organization_id=context.require_organization(), on=on, patient_id=patient_id
    )
    return [
        AppointmentRow(
            **AppointmentResponse.model_validate(v.appointment).model_dump(),
            patient_code=v.patient_code,
            patient_name=v.patient_name,
            patient_phone=v.patient_phone,
            dentist_name=v.dentist_name,
            encounter_id=v.encounter_id,
        )
        for v in views
    ]


@router.get(
    "/dentists",
    response_model=list[DentistRow],
    dependencies=[Depends(require_permissions("clinic.appointment.read"))],
)
async def list_bookable_dentists(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[DentistRow]:
    """Who a booking may be given to.

    Read with `clinic.appointment.read`, not `identity.user.read`: the
    receptionist who books the day is not thereby allowed the staff
    directory. It answers a name and an id, both of which already appear in
    the day's list beside it.
    """
    rows = await TreatmentService(db, audit).bookable_dentists(
        organization_id=context.require_organization()
    )
    return [DentistRow(id=user_id, full_name=name) for user_id, name in rows]


@router.post(
    "/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("clinic.appointment.manage"))],
)
async def book_appointment(
    payload: AppointmentCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AppointmentResponse:
    """Book a patient in.

    The date and time are the clinic's own, stored as a local date and a
    local time. One dentist cannot be in two chairs: an overlap with a live
    booking for the same dentist is refused, and the refusal says which
    booking it clashed with. A booking with no time clashes with nothing.
    """
    appointment = await TreatmentService(db, audit).book(
        organization_id=context.require_organization(),
        patient_id=payload.patient_id,
        scheduled_on=payload.scheduled_on,
        scheduled_at=payload.scheduled_at,
        dentist_user_id=payload.dentist_user_id,
        duration_minutes=payload.duration_minutes,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AppointmentResponse.model_validate(appointment)


@router.post(
    "/appointments/{appointment_id}/reschedule",
    response_model=AppointmentResponse,
    dependencies=[Depends(require_permissions("clinic.appointment.manage"))],
)
async def reschedule_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentRescheduleRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AppointmentResponse:
    """Move a booking, keeping the appointment.

    Not a cancellation and a new booking: the audit record shows where it
    moved from, which a cancel-and-rebook pair does not. The new time is
    checked against the dentist's other bookings like any other.
    """
    appointment = await TreatmentService(db, audit).reschedule(
        organization_id=context.require_organization(),
        appointment_id=appointment_id,
        scheduled_on=payload.scheduled_on,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        dentist_user_id=payload.dentist_user_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AppointmentResponse.model_validate(appointment)


@router.post(
    "/appointments/{appointment_id}/status",
    response_model=AppointmentResponse,
    dependencies=[Depends(require_permissions("clinic.appointment.manage"))],
)
async def set_appointment_status(
    appointment_id: uuid.UUID,
    payload: AppointmentStatusRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> AppointmentResponse:
    """Confirm, admit, complete, cancel or mark a no-show.

    None of these is an accounting event. An attendance that produces no
    treatment produces no revenue, which the clinic's own register shows
    happening several times a week.
    """
    appointment = await TreatmentService(db, audit).set_appointment_status(
        organization_id=context.require_organization(),
        appointment_id=appointment_id,
        status=payload.status,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return AppointmentResponse.model_validate(appointment)


# -------------------------------------------------------------- encounters


@router.post(
    "/encounters",
    response_model=EncounterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def open_encounter(
    payload: EncounterOpenRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EncounterResponse:
    """Begin a visit. A walk-in without an appointment is allowed."""
    encounter = await TreatmentService(db, audit).open_encounter(
        organization_id=context.require_organization(),
        patient_id=payload.patient_id,
        occurred_on=payload.occurred_on,
        appointment_id=payload.appointment_id,
        dentist_user_id=payload.dentist_user_id,
        complaint=payload.complaint,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return EncounterResponse.model_validate(encounter)


@router.post(
    "/encounters/{encounter_id}/complete",
    response_model=EncounterResponse,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def complete_encounter(
    encounter_id: uuid.UUID,
    payload: EncounterCompleteRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> EncounterResponse:
    """End the visit. Still nothing posted: a completed visit is not revenue."""
    encounter = await TreatmentService(db, audit).complete_encounter(
        organization_id=context.require_organization(),
        encounter_id=encounter_id,
        clinical_notes=payload.clinical_notes,
        body_weight_kg=payload.body_weight_kg,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return EncounterResponse.model_validate(encounter)


@router.get(
    "/patients/{patient_id}/encounters",
    response_model=list[EncounterResponse],
    dependencies=[Depends(require_permissions("clinic.clinical.read"))],
)
async def list_encounters(
    patient_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[EncounterResponse]:
    rows = await TreatmentService(db, audit).list_encounters(
        organization_id=context.require_organization(), patient_id=patient_id
    )
    await _record_clinical_read(
        db=db, audit=audit, context=context, target_type="patient", target_id=patient_id
    )
    return [EncounterResponse.model_validate(r) for r in rows]


# ----------------------------------------------------------- treatment items


@router.post(
    "/encounters/{encounter_id}/items",
    response_model=list[TreatmentItemResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def record_items(
    encounter_id: uuid.UUID,
    payload: RecordItemsRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[TreatmentItemResponse]:
    """Record work against a visit, one row per tooth."""
    items = await TreatmentService(db, audit).record_items(
        organization_id=context.require_organization(),
        encounter_id=encounter_id,
        items=[_item_input(i) for i in payload.items],
        plan_id=payload.plan_id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return [TreatmentItemResponse.model_validate(i) for i in items]


@router.get(
    "/patients/{patient_id}/treatments",
    response_model=list[TreatmentItemResponse],
    dependencies=[Depends(require_permissions("clinic.clinical.read"))],
)
async def list_treatments(
    patient_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[TreatmentItemResponse]:
    rows = await TreatmentService(db, audit).list_items(
        organization_id=context.require_organization(), patient_id=patient_id
    )
    await _record_clinical_read(
        db=db, audit=audit, context=context, target_type="patient", target_id=patient_id
    )
    return [TreatmentItemResponse.model_validate(r) for r in rows]


@router.post(
    "/treatments/{item_id}/complete",
    response_model=TreatmentItemResponse,
    dependencies=[Depends(require_permissions("clinic.treatment.complete"))],
)
async def complete_item(
    item_id: uuid.UUID,
    payload: CompleteItemRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> TreatmentItemResponse:
    """Mark one piece of work finished, which makes it billable — only that.

    Nothing is posted. The revenue is recognised when an invoice is issued,
    by somebody holding `sales.invoice.manage`.
    """
    item = await TreatmentService(db, audit).complete_item(
        organization_id=context.require_organization(),
        item_id=item_id,
        completed_on=payload.completed_on,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return TreatmentItemResponse.model_validate(item)


@router.post(
    "/treatments/{item_id}/cancel",
    response_model=TreatmentItemResponse,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def cancel_item(
    item_id: uuid.UUID,
    payload: ReasonRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> TreatmentItemResponse:
    item = await TreatmentService(db, audit).cancel_item(
        organization_id=context.require_organization(),
        item_id=item_id,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return TreatmentItemResponse.model_validate(item)


@router.post(
    "/treatments/{item_id}/correct",
    response_model=TreatmentItemResponse,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def correct_item(
    item_id: uuid.UUID,
    payload: CorrectItemRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> TreatmentItemResponse:
    """Supersede a recorded treatment with what really happened.

    The original is kept and marked, pointing at its replacement. A clinical
    record that can be edited in place is not a clinical record.
    """
    item = await TreatmentService(db, audit).correct_item(
        organization_id=context.require_organization(),
        item_id=item_id,
        replacement=_item_input(payload.replacement),
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return TreatmentItemResponse.model_validate(item)


@router.get(
    "/treatments/{item_id}/materials",
    response_model=list[MovementResponse],
    dependencies=[Depends(require_permissions("clinic.clinical.read"))],
)
async def list_treatment_materials(
    item_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[MovementResponse]:
    """What was used on this tooth, read back from the stock ledger.

    There is no `treatment_material` table: the movement already records
    which treatment consumed it, and a second row saying the same thing
    would be a second answer to one question.
    """
    organization_id = context.require_organization()
    await TreatmentService(db, audit).get_item(organization_id=organization_id, item_id=item_id)
    rows = await StockService(db, audit).movements(
        organization_id=organization_id, limit=1000, kind=StockMovementKind.CONSUMPTION
    )
    return [MovementResponse.model_validate(row) for row in rows if row.source_id == item_id]


@router.post(
    "/treatments/{item_id}/materials",
    response_model=MovementResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("inventory.consumption.record"))],
)
async def record_treatment_material(
    item_id: uuid.UUID,
    payload: TreatmentMaterialRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> MovementResponse:
    """Record a material used on this tooth. **Posts nothing.**

    The treatment is loaded first, so a quantity can never be taken off the
    shelf against a treatment that does not exist or belongs to another
    clinic. What the material cost is already in the books from the supplier
    invoice; moving that cost to an expense as it is used needs the
    valuation method nobody has declared (`OPEN_QUESTIONS.md` I1).
    """
    organization_id = context.require_organization()
    treatment = await TreatmentService(db, audit).get_item(
        organization_id=organization_id, item_id=item_id
    )
    movement = await StockService(db, audit).consume(
        organization_id=organization_id,
        item_id=payload.item_id,
        quantity=payload.quantity,
        location_id=payload.location_id,
        batch_id=payload.batch_id,
        treatment_item_id=treatment.id,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return MovementResponse.model_validate(movement)


@router.post(
    "/plans",
    response_model=TreatmentPlanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def create_plan(
    payload: PlanCreateRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> TreatmentPlanResponse:
    """A course of work spanning several visits, as prosthetics always does."""
    plan = await TreatmentService(db, audit).create_plan(
        organization_id=context.require_organization(),
        patient_id=payload.patient_id,
        title=payload.title,
        dentist_user_id=payload.dentist_user_id,
        notes=payload.notes,
    )
    await db.commit()
    return TreatmentPlanResponse.model_validate(plan)


@router.get(
    "/plans",
    response_model=list[TreatmentPlanResponse],
    dependencies=[Depends(require_permissions("clinic.clinical.read"))],
)
async def list_plans(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    patient_id: uuid.UUID | None = Query(default=None),
    status_filter: list[PlanStatus] | None = Query(default=None, alias="status"),
) -> list[TreatmentPlanResponse]:
    """Courses of treatment, newest first.

    Behind `clinic.clinical.read` rather than `clinic.patient.read`: a plan
    names the work a person is having done, which is clinical information.
    """
    plans = await TreatmentService(db, audit).list_plans(
        organization_id=context.require_organization(),
        patient_id=patient_id,
        statuses=list(status_filter) if status_filter else None,
    )
    return [TreatmentPlanResponse.model_validate(p) for p in plans]


@router.get(
    "/plans/{plan_id}",
    response_model=TreatmentPlanDetail,
    dependencies=[Depends(require_permissions("clinic.clinical.read"))],
)
async def get_plan(
    plan_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> TreatmentPlanDetail:
    organization_id = context.require_organization()
    service = TreatmentService(db, audit)
    plan = await service.get_plan(organization_id=organization_id, plan_id=plan_id)
    items = await service.plan_items(organization_id=organization_id, plan_id=plan_id)
    await _record_clinical_read(
        db=db,
        audit=audit,
        context=context,
        target_type="treatment_plan",
        target_id=plan.id,
        target_label=plan.code,
    )
    return TreatmentPlanDetail(
        **TreatmentPlanResponse.model_validate(plan).model_dump(),
        dentist_user_id=plan.dentist_user_id,
        items=[TreatmentItemResponse.model_validate(i) for i in items],
        open_item_count=sum(1 for i in items if i.status in OPEN_ITEM_STATUSES),
    )


@router.post(
    "/plans/{plan_id}/status",
    response_model=TreatmentPlanResponse,
    dependencies=[Depends(require_permissions("clinic.clinical.write"))],
)
async def set_plan_status(
    plan_id: uuid.UUID,
    payload: PlanStatusRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> TreatmentPlanResponse:
    """Propose a plan, record the patient's agreement, start, finish or abandon it."""
    plan = await TreatmentService(db, audit).set_plan_status(
        organization_id=context.require_organization(),
        plan_id=plan_id,
        status=payload.status,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return TreatmentPlanResponse.model_validate(plan)


# ----------------------------------------------------------------- billing


@router.get(
    "/patients/{patient_id}/billable",
    response_model=list[TreatmentItemResponse],
    dependencies=[Depends(require_permissions("clinic.clinical.read"))],
)
async def billable_items(
    patient_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[TreatmentItemResponse]:
    """Completed work not yet on an invoice."""
    rows = await TreatmentService(db, audit).billable_items(
        organization_id=context.require_organization(), patient_id=patient_id
    )
    return [TreatmentItemResponse.model_validate(r) for r in rows]


@router.post(
    "/patients/{patient_id}/invoices",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("sales.invoice.manage"))],
)
async def bill_completed_items(
    patient_id: uuid.UUID,
    payload: BillItemsRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> InvoiceResponse:
    """Draft an invoice from completed work.

    A **draft**. Issuing it recognises the revenue and is a separate act with
    its own permission, under `/sales/invoices/{id}/issue`.
    """
    organization_id = context.require_organization()
    invoice = await TreatmentService(db, audit).bill_completed_items(
        organization_id=organization_id,
        patient_id=patient_id,
        item_ids=payload.item_ids,
        issue_date=payload.issue_date,
        actor_user_id=context.user_id,
    )
    await db.commit()
    row = InvoiceResponse.model_validate(invoice)
    row.subtotal = quantize(invoice.subtotal, invoice.currency)
    row.tax_total = quantize(invoice.tax_total, invoice.currency)
    row.total = quantize(invoice.total, invoice.currency)
    row.allocated = ZERO
    row.outstanding = row.total
    return row


# -------------------------------------------------------------- settlement


@router.get(
    "/invoices/{invoice_id}/settlement",
    response_model=SettlementView,
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def settlement_view(
    invoice_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SettlementView:
    """What this bill comes to, what has been paid, what is still owed.

    The remaining balance the payment panel shows. Computed here from the
    invoice total and its live allocations every time it is asked for, so the
    panel cannot display a figure that is merely the last one it was told.
    """
    view = await SettlementService(db, audit).view(
        organization_id=context.require_organization(), invoice_id=invoice_id
    )
    return SettlementView(**asdict(view))


@router.post(
    "/invoices/{invoice_id}/settlement",
    response_model=SettlementResult,
    dependencies=[Depends(require_permissions("sales.payment.record"))],
)
async def settle_invoice(
    invoice_id: uuid.UUID,
    payload: SettlementRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> SettlementResult:
    """Take a payment: cash, card, an applied advance, or any mixture.

    One submission, one transaction. The total is checked against the
    remaining balance under the invoice's row lock, so cash and card cannot
    each pass a check the pair of them fails. Overpayment is refused, and
    refused again by a trigger on the allocation table.

    `request_key` makes the submission idempotent: send the same key again —
    a double-click, a retry, a refreshed page — and the first outcome is
    returned rather than the money being taken twice.
    """
    organization_id = context.require_organization()
    result = await SettlementService(db, audit).settle(
        organization_id=organization_id,
        invoice_id=invoice_id,
        request_key=payload.request_key,
        cash=payload.cash,
        card=payload.card,
        advances=payload.advances,
        received_on=payload.received_on,
        cash_session_id=payload.cash_session_id,
        card_reference=payload.card_reference,
        actor_user_id=context.user_id,
    )
    await db.commit()
    return SettlementResult(
        view=SettlementView(**asdict(result.view)),
        applied_advances=result.applied_advances,
        cash_payment_id=result.cash_payment_id,
        card_payment_id=result.card_payment_id,
        replayed=result.replayed,
    )


@router.get(
    "/invoices/{invoice_id}/settlement/history",
    response_model=list[SettlementHistoryRow],
    dependencies=[Depends(require_permissions("sales.invoice.read"))],
)
async def settlement_history(
    invoice_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> list[SettlementHistoryRow]:
    """Every allocation ever made against this bill, reversals included."""
    rows = await SettlementService(db, audit).history(
        organization_id=context.require_organization(), invoice_id=invoice_id
    )
    return [SettlementHistoryRow(**row) for row in rows]
