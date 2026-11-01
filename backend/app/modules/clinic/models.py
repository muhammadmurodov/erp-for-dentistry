"""Clinical records: patients, appointments, encounters, treatment items.

One rule governs the shapes here: **the clinic module stores no money.**

A patient's debt is not a column on `patient`; it is read from the sales
service, which derives it from invoices and live allocations. A treatment
item does not carry what the patient owes; it carries the price basis used
when it was billed, and the invoice line it produced. Anything else would be
a second receivables ledger running alongside the real one, free to disagree
with it — which is exactly what the clinic has today, with a paper register
on one side and a spreadsheet on the other.

The second rule is that clinical detail is separable from billing detail.
`Patient` is readable with `clinic.patient.read`, which reception and billing
staff hold. `Encounter`, `TreatmentPlan` and `TreatmentItem` carry findings
and notes and are readable only with `clinic.clinical.read`, which they do
not.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.money import MoneyType
from app.modules.clinic.enums import (
    TOOTH_NUMBER_SQL,
    AppointmentStatus,
    DuplicateReviewStatus,
    EncounterStatus,
    PlanStatus,
    TreatmentItemStatus,
)


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


class Patient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A person the clinic treats.

    Almost every field is nullable, and that is deliberate rather than lax:
    the clinic's own register leaves the birth year and the address blank on
    a good proportion of rows, and records the year of birth rather than the
    date. A form that insisted on a complete record would simply be filled
    in with invented values.

    `customer_id` is the one link to the money. The patient *is* the payer in
    the ordinary case, but the two stay separate records so that somebody
    else can pay — a parent, an employer — without the clinical record
    acquiring their billing details.
    """

    __tablename__ = "patient"

    organization_id: Mapped[uuid.UUID] = _org_column()
    #: Stable identifier the clinic has never had. Generated, never reused.
    code: Mapped[str] = mapped_column(String(32), nullable=False)

    family_name: Mapped[str] = mapped_column(String(128), nullable=False)
    given_name: Mapped[str] = mapped_column(String(128), nullable=False)
    patronymic: Mapped[str | None] = mapped_column(String(128))
    #: Denormalised for search and display, maintained by the service.
    display_name: Mapped[str] = mapped_column(String(400), nullable=False)

    #: The register records a year, not a date, so the year is what the system
    #: asks for. A full date is accepted when it is actually known.
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    birth_date: Mapped[date | None] = mapped_column(Date)

    #: As the patient gave it, for display and for dialling.
    phone: Mapped[str | None] = mapped_column(String(32))
    #: Digits only, for searching. **Deliberately not unique**: relatives
    #: share a number, and a household often gives one contact for everybody.
    phone_normalized: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    registered_on: Mapped[date] = mapped_column(Date, nullable=False)
    assigned_dentist_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Set when this record has been merged into another. The row stays: the
    #: treatments recorded against it happened, and the audit trail has to be
    #: able to explain where they went.
    merged_into_patient_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("patient"),
        UniqueConstraint("organization_id", "code", name="uq_patient_organization_id_code"),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customer.organization_id", "customer.id"],
            ondelete="RESTRICT",
            name="fk_patient_customer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "merged_into_patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="RESTRICT",
            name="fk_patient_merged_into",
        ),
        CheckConstraint(
            "birth_year IS NULL OR birth_year BETWEEN 1900 AND 2200",
            name="birth_year_plausible",
        ),
        CheckConstraint(
            "merged_into_patient_id IS NULL OR merged_into_patient_id <> id",
            name="not_merged_into_itself",
        ),
        CheckConstraint(
            "(merged_into_patient_id IS NULL) = (merged_at IS NULL)",
            name="merge_has_timestamp",
        ),
        Index("ix_patient_name", "organization_id", "family_name", "given_name"),
        Index("ix_patient_phone", "organization_id", "phone_normalized"),
    )


class PatientDuplicateReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Two records that may be the same person, waiting for a decision.

    Matching proposes; a person disposes. The clinic identifies patients by
    name and birth year, so near-matches are common and genuinely ambiguous —
    two brothers a year apart, a married name, a misheard spelling. Merging
    automatically would silently join two people's clinical histories, which
    is the one mistake in this module that could actually hurt somebody.
    """

    __tablename__ = "patient_duplicate_review"

    organization_id: Mapped[uuid.UUID] = _org_column()
    patient_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    candidate_patient_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: Why the system thought so, in words a receptionist can act on.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DuplicateReviewStatus] = mapped_column(
        _enum(DuplicateReviewStatus, "duplicate_review_status"),
        nullable=False,
        default=DuplicateReviewStatus.PENDING,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    decision_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("patient_duplicate_review"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="CASCADE",
            name="fk_duplicate_review_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "candidate_patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="CASCADE",
            name="fk_duplicate_review_candidate",
        ),
        UniqueConstraint(
            "organization_id",
            "patient_id",
            "candidate_patient_id",
            name="uq_duplicate_review_pair",
        ),
        CheckConstraint("patient_id <> candidate_patient_id", name="not_its_own_duplicate"),
        CheckConstraint(
            "(status = 'PENDING') = (decided_at IS NULL)",
            name="decision_has_timestamp",
        ),
        Index("ix_duplicate_review_queue", "organization_id", "status"),
    )


class Appointment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A booking. Reception can see it; it carries no clinical detail.

    `reason` is what the patient said on the telephone, not a diagnosis.
    """

    __tablename__ = "appointment"

    organization_id: Mapped[uuid.UUID] = _org_column()
    patient_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    dentist_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    scheduled_on: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(Time)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[AppointmentStatus] = mapped_column(
        _enum(AppointmentStatus, "appointment_status"),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
    )
    reason: Mapped[str | None] = mapped_column(Text)
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("appointment"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="RESTRICT",
            name="fk_appointment_patient",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes BETWEEN 5 AND 600",
            name="duration_plausible",
        ),
        Index("ix_appointment_day", "organization_id", "scheduled_on", "status"),
        Index("ix_appointment_patient", "organization_id", "patient_id"),
    )


class TreatmentPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A course of work, usually spanning several visits.

    The clinic's register shows prosthetic work running across days as a
    matter of course — an impression on one day, a fitting later. The plan is
    what holds those together; it is not a quotation and not an invoice.
    """

    __tablename__ = "treatment_plan"

    organization_id: Mapped[uuid.UUID] = _org_column()
    patient_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    dentist_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PlanStatus] = mapped_column(
        _enum(PlanStatus, "plan_status"), nullable=False, default=PlanStatus.DRAFT
    )
    started_on: Mapped[date | None] = mapped_column(Date)
    completed_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        *_org_scope("treatment_plan"),
        UniqueConstraint("organization_id", "code", name="uq_treatment_plan_organization_id_code"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="RESTRICT",
            name="fk_treatment_plan_patient",
        ),
        Index("ix_treatment_plan_patient", "organization_id", "patient_id", "status"),
    )


class Encounter(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One visit to the chair. The clinical record proper.

    Readable only with `clinic.clinical.read`. A patient may attend without
    an appointment, so `appointment_id` is optional; and an encounter may end
    with nothing billable at all, which is the ordinary outcome of a
    consultation.
    """

    __tablename__ = "encounter"

    organization_id: Mapped[uuid.UUID] = _org_column()
    patient_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    dentist_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[EncounterStatus] = mapped_column(
        _enum(EncounterStatus, "encounter_status"),
        nullable=False,
        default=EncounterStatus.OPEN,
    )
    #: What the patient came in saying, in their words.
    complaint: Mapped[str | None] = mapped_column(Text)
    #: What the dentist found and did. Clinical detail; never on an invoice.
    clinical_notes: Mapped[str | None] = mapped_column(Text)
    #: Occasionally recorded for children, where it affects dosing. The
    #: clinic's paper register does exactly this.
    body_weight_kg: Mapped[Decimal | None] = mapped_column(MoneyType)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        *_org_scope("encounter"),
        UniqueConstraint("organization_id", "code", name="uq_encounter_organization_id_code"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="RESTRICT",
            name="fk_encounter_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "appointment_id"],
            ["appointment.organization_id", "appointment.id"],
            ondelete="SET NULL",
            name="fk_encounter_appointment",
        ),
        CheckConstraint(
            "body_weight_kg IS NULL OR (body_weight_kg > 0 AND body_weight_kg < 500)",
            name="body_weight_plausible",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR completed_at IS NOT NULL",
            name="completed_has_timestamp",
        ),
        Index("ix_encounter_patient", "organization_id", "patient_id", "occurred_on"),
        Index("ix_encounter_day", "organization_id", "occurred_on"),
    )


class TreatmentItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One piece of work, on one tooth, with one status.

    Per tooth rather than per visit, because the register shows three and four
    teeth treated in a single sitting, each with its own finding. And an item
    survives its encounter: the same tooth appears on consecutive days as the
    work progresses, so an item begun at one visit is completed at another.

    **It holds no amount the patient owes.** `unit_price` is the price basis
    at the moment of billing, copied so a later price change cannot restate
    history; `invoice_line_id` points at the line that is actually
    authoritative. Until it is billed, both are empty — a completed treatment
    is not yet revenue.
    """

    __tablename__ = "treatment_item"

    organization_id: Mapped[uuid.UUID] = _org_column()
    patient_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    service_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    dentist_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    #: FDI notation, the scheme the clinic already writes in its register.
    #: Null for work that is not tooth-specific — a consultation, a scale.
    tooth_number: Mapped[int | None] = mapped_column(SmallInteger)
    #: Which surfaces, where that matters: free text such as "MOD".
    tooth_surfaces: Mapped[str | None] = mapped_column(String(16))
    #: The dentist's finding for this tooth. Clinical; never on an invoice.
    finding: Mapped[str | None] = mapped_column(Text)

    status: Mapped[TreatmentItemStatus] = mapped_column(
        _enum(TreatmentItemStatus, "treatment_item_status"),
        nullable=False,
        default=TreatmentItemStatus.PLANNED,
    )
    planned_on: Mapped[date | None] = mapped_column(Date)
    completed_on: Mapped[date | None] = mapped_column(Date)
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    #: Set on the superseded item when a correction is recorded.
    corrected_by_item_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    quantity: Mapped[Decimal] = mapped_column(MoneyType, nullable=False, default=Decimal("1"))
    #: The price used at billing. Empty until then — see the class docstring.
    unit_price: Mapped[Decimal | None] = mapped_column(MoneyType)
    #: The invoice line this item produced. The money lives there, not here.
    invoice_line_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    billed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        *_org_scope("treatment_item"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patient.organization_id", "patient.id"],
            ondelete="RESTRICT",
            name="fk_treatment_item_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "encounter_id"],
            ["encounter.organization_id", "encounter.id"],
            ondelete="SET NULL",
            name="fk_treatment_item_encounter",
        ),
        ForeignKeyConstraint(
            ["organization_id", "plan_id"],
            ["treatment_plan.organization_id", "treatment_plan.id"],
            ondelete="SET NULL",
            name="fk_treatment_item_plan",
        ),
        ForeignKeyConstraint(
            ["organization_id", "service_id"],
            ["service.organization_id", "service.id"],
            ondelete="RESTRICT",
            name="fk_treatment_item_service",
        ),
        ForeignKeyConstraint(
            ["organization_id", "invoice_line_id"],
            ["invoice_line.organization_id", "invoice_line.id"],
            ondelete="RESTRICT",
            name="fk_treatment_item_invoice_line",
        ),
        ForeignKeyConstraint(
            ["organization_id", "corrected_by_item_id"],
            ["treatment_item.organization_id", "treatment_item.id"],
            ondelete="RESTRICT",
            name="fk_treatment_item_corrected_by",
        ),
        CheckConstraint(TOOTH_NUMBER_SQL, name="tooth_number_is_fdi"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="unit_price_not_negative"),
        CheckConstraint(
            "status <> 'COMPLETED' OR completed_on IS NOT NULL",
            name="completed_has_a_date",
        ),
        # Billing is the one thing here that touches money, so the database
        # states the rule too: only completed work is ever billed, and a
        # billed item carries both the line and the price it went out at.
        CheckConstraint(
            "invoice_line_id IS NULL OR status = 'COMPLETED'",
            name="only_completed_work_is_billed",
        ),
        CheckConstraint(
            "(invoice_line_id IS NULL) = (billed_at IS NULL)",
            name="billed_has_timestamp",
        ),
        CheckConstraint(
            "invoice_line_id IS NULL OR unit_price IS NOT NULL",
            name="billed_has_a_price",
        ),
        Index("ix_treatment_item_patient", "organization_id", "patient_id", "status"),
        Index("ix_treatment_item_encounter", "organization_id", "encounter_id"),
        # The billable queue: completed work not yet on an invoice.
        Index(
            "ix_treatment_item_billable",
            "organization_id",
            "patient_id",
            postgresql_where=text("status = 'COMPLETED' AND invoice_line_id IS NULL"),
        ),
    )


class SettlementRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One submission of the payment form, remembered so it cannot be replayed.

    The exception to the module's no-money rule, and the reason it earns one:
    this table holds **no balance**. It records that a particular submission
    was already carried out and what it produced, so that the same submission
    arriving twice — a double-click, a retried request, a cashier refreshing
    a page that did not seem to respond — settles the invoice once and then
    reports the first outcome again instead of taking the money twice.

    `request_key` is supplied by the client and unique per organization. The
    amounts are kept to tell a genuine replay from a key reused by accident
    for a different submission: the first is answered with the original
    result, the second is refused.

    The money itself is still entirely in `sales` and `cash`: the columns here
    are the request as submitted, plus the ids of the payments it created.
    """

    __tablename__ = "settlement_request"

    organization_id: Mapped[uuid.UUID] = _org_column()
    invoice_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: Client-generated. A UUID in practice; any stable string will do.
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)

    requested_cash: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    requested_card: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)
    requested_advances: Mapped[Decimal] = mapped_column(MoneyType, nullable=False)

    cash_payment_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    card_payment_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    applied_advances: Mapped[Decimal | None] = mapped_column(MoneyType)
    #: What was left owing once this submission was applied. A record of the
    #: answer given, not a balance anything reads back.
    remaining_after: Mapped[Decimal | None] = mapped_column(MoneyType)

    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        *_org_scope("settlement_request"),
        UniqueConstraint("organization_id", "request_key", name="uq_settlement_request_key"),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoice.organization_id", "invoice.id"],
            ondelete="RESTRICT",
            name="fk_settlement_request_invoice",
        ),
        CheckConstraint("requested_cash >= 0", name="cash_not_negative"),
        CheckConstraint("requested_card >= 0", name="card_not_negative"),
        CheckConstraint("requested_advances >= 0", name="advances_not_negative"),
        CheckConstraint(
            "requested_cash + requested_card + requested_advances > 0",
            name="settlement_is_not_empty",
        ),
        Index("ix_settlement_request_invoice", "organization_id", "invoice_id"),
    )


__all__ = [
    "Appointment",
    "Encounter",
    "Patient",
    "PatientDuplicateReview",
    "SettlementRequest",
    "TreatmentItem",
    "TreatmentPlan",
]
