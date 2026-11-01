"""Clinic request and response shapes. Money travels as decimal strings.

Two rules shape this file.

**Clinical detail and billing detail are separate payloads.** `PatientSummary`
carries a name, a telephone number and a code; it is what reception and the
cashier see. Findings, complaints and notes live on the encounter and item
responses, which are only ever returned behind `clinic.clinical.read`. Mixing
them into one convenient response would hand a clinical record to everybody
who can take a payment.

**No amount a client sends is ever trusted as a balance.** `SettlementRequest`
accepts what the patient is handing over — cash, card, an advance to apply —
and nothing else. The remaining balance is absent from every request shape in
this file, because it is not an input: it is computed from the invoice and its
allocations, and a field for it would be a field somebody could edit.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.schemas import MoneyMixin
from app.modules.clinic.enums import (
    AppointmentStatus,
    DuplicateReviewStatus,
    EncounterStatus,
    PlanStatus,
    SettlementStatus,
    TreatmentItemStatus,
)
from app.modules.sales.enums import InvoiceStatus, PaymentMethod


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("Send monetary amounts as strings, not JSON numbers")
    return value


# ---------------------------------------------------------------- patients


class PatientSummary(BaseModel):
    """A patient as reception and billing see them. No clinical content."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str
    family_name: str
    given_name: str
    patronymic: str | None
    birth_year: int | None
    phone: str | None
    address: str | None
    registered_on: date
    assigned_dentist_user_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    is_active: bool
    merged_into_patient_id: uuid.UUID | None


class DuplicateCandidateResponse(BaseModel):
    """Somebody already on file who may be this person, and why."""

    patient_id: uuid.UUID
    code: str
    display_name: str
    birth_year: int | None
    phone: str | None
    reason: str


class PatientRegisterRequest(BaseModel):
    family_name: str = Field(min_length=1, max_length=128)
    given_name: str = Field(min_length=1, max_length=128)
    patronymic: str | None = Field(default=None, max_length=128)
    #: A year, not a date: the clinic's register records the year alone, and
    #: leaves it blank often enough that demanding it would get it invented.
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = None
    notes: str | None = None
    assigned_dentist_user_id: uuid.UUID | None = None
    registered_on: date | None = None
    #: Register anyway, having been shown the candidates. Registration is
    #: never blocked by a suspected duplicate — the receptionist has the
    #: patient in front of them — so this only suppresses raising the review
    #: a second time for a decision somebody has already taken.
    allow_duplicate: bool = False


class PatientUpdateRequest(BaseModel):
    family_name: str | None = Field(default=None, min_length=1, max_length=128)
    given_name: str | None = Field(default=None, min_length=1, max_length=128)
    patronymic: str | None = Field(default=None, max_length=128)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = None
    notes: str | None = None
    assigned_dentist_user_id: uuid.UUID | None = None
    is_active: bool | None = None


class PatientRegistered(BaseModel):
    """The new record, plus whoever it might be a duplicate of."""

    patient: PatientSummary
    duplicate_candidates: list[DuplicateCandidateResponse] = []


class PatientBalance(BaseModel, MoneyMixin):
    """What the patient owes and what they have left with the clinic.

    Read from the sales service, which derives it from invoices and live
    allocations. The clinic module stores none of these figures.
    """

    customer_id: uuid.UUID | None
    invoiced: Decimal
    paid: Decimal
    #: The qarzdorlik: billed, not paid, not written off.
    outstanding: Decimal
    #: Oldindan to'lov — money received that no invoice has claimed yet.
    advances_available: Decimal
    written_off: Decimal


class PatientInvoiceRow(BaseModel, MoneyMixin):
    """One of the patient's bills, with its balance computed."""

    invoice_id: uuid.UUID
    number: str
    issue_date: date
    status: InvoiceStatus
    settlement_status: SettlementStatus
    total: Decimal
    paid: Decimal
    remaining: Decimal
    currency: str


class PatientDetail(BaseModel):
    """The patient page: who they are, what they owe, what is outstanding.

    Deliberately free of clinical content so that the page a cashier opens to
    take a payment does not also show them a diagnosis.
    """

    patient: PatientSummary
    balance: PatientBalance
    invoices: list[PatientInvoiceRow] = []
    #: Completed work not yet on any invoice.
    billable_items: int = 0
    open_duplicate_reviews: int = 0


class MergeRequest(BaseModel):
    into_patient_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=1000)


class DuplicateReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    candidate_patient_id: uuid.UUID
    reason: str
    status: DuplicateReviewStatus
    decided_at: datetime | None
    decision_note: str | None


class DuplicateReviewDetail(BaseModel):
    """A pending duplicate with both records attached, ready to compare.

    The queue exists because name and birth year is the only key this clinic
    has ever used, so two rows that look alike are expected rather than
    exceptional. Deciding between them needs the two records side by side, and
    an endpoint that returned ids would have every screen fetch them one at a
    time and show whichever arrived first.

    `survivor_id` says which record a merge would keep — the one already on
    file, not the one just typed — so the screen can name it before anybody
    presses anything, rather than reporting it afterwards.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str
    status: DuplicateReviewStatus
    created_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    #: The record the duplicate was raised *for*: normally the newer one.
    patient: PatientSummary
    #: The record it may be a duplicate of.
    candidate: PatientSummary
    survivor_id: uuid.UUID


class ResolveReviewRequest(BaseModel):
    #: True merges the two records; False records that they are different
    #: people, so the pair is never raised again.
    merge: bool
    note: str = Field(min_length=3, max_length=1000)


# ------------------------------------------------------------ appointments


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    dentist_user_id: uuid.UUID | None
    scheduled_on: date
    duration_minutes: int | None
    status: AppointmentStatus
    reason: str | None
    cancelled_reason: str | None


class AppointmentCreateRequest(BaseModel):
    patient_id: uuid.UUID
    scheduled_on: date
    dentist_user_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    reason: str | None = None


class AppointmentStatusRequest(BaseModel):
    status: AppointmentStatus
    reason: str | None = None


# -------------------------------------------------------------- encounters


class EncounterResponse(BaseModel):
    """Behind `clinic.clinical.read`: carries the complaint and the notes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    patient_id: uuid.UUID
    appointment_id: uuid.UUID | None
    dentist_user_id: uuid.UUID | None
    occurred_on: date
    status: EncounterStatus
    complaint: str | None
    clinical_notes: str | None
    body_weight_kg: Decimal | None
    completed_at: datetime | None


class EncounterOpenRequest(BaseModel):
    patient_id: uuid.UUID
    occurred_on: date | None = None
    appointment_id: uuid.UUID | None = None
    dentist_user_id: uuid.UUID | None = None
    complaint: str | None = None


class EncounterCompleteRequest(BaseModel):
    clinical_notes: str | None = None
    #: Clinically meaningful for a child — the clinic records it for dosing —
    #: and nothing to do with the bill.
    body_weight_kg: Decimal | None = Field(default=None, gt=0, le=400)


# --------------------------------------------------------- treatment items


class TreatmentItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None
    plan_id: uuid.UUID | None
    service_id: uuid.UUID
    dentist_user_id: uuid.UUID | None
    tooth_number: int | None
    tooth_surfaces: str | None
    finding: str | None
    status: TreatmentItemStatus
    planned_on: date | None
    completed_on: date | None
    quantity: Decimal
    #: Empty until the item is billed. The clinical record does not decide
    #: the price; the invoice line it produced is authoritative.
    unit_price: Decimal | None
    invoice_line_id: uuid.UUID | None
    corrected_by_item_id: uuid.UUID | None


class TreatmentItemInput(BaseModel):
    service_id: uuid.UUID
    #: FDI notation, as the clinic already writes it. Validated against the
    #: real tooth numbers, not merely against a range.
    tooth_number: int | None = Field(default=None, ge=11, le=85)
    tooth_surfaces: str | None = Field(default=None, max_length=16)
    finding: str | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    status: TreatmentItemStatus = TreatmentItemStatus.PLANNED
    planned_on: date | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)


class RecordItemsRequest(BaseModel):
    items: list[TreatmentItemInput] = Field(min_length=1)
    plan_id: uuid.UUID | None = None


class CompleteItemRequest(BaseModel):
    completed_on: date | None = None


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CorrectItemRequest(BaseModel):
    replacement: TreatmentItemInput
    reason: str = Field(min_length=3, max_length=1000)


class PlanCreateRequest(BaseModel):
    patient_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    dentist_user_id: uuid.UUID | None = None
    notes: str | None = None


class TreatmentPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    patient_id: uuid.UUID
    title: str
    status: PlanStatus
    started_on: date | None
    completed_on: date | None
    notes: str | None


class BillItemsRequest(BaseModel):
    #: Omitted bills everything completed and unbilled, which is how the
    #: clinic settles a course of treatment at the end.
    item_ids: list[uuid.UUID] | None = None
    issue_date: date | None = None


# ------------------------------------------------------------- settlement


class SettlementView(BaseModel, MoneyMixin):
    """The payment panel's state. Every figure derived, none of it editable.

    `remaining` is the number the specification calls the remaining balance.
    It is sent to the client for display and is **not** accepted back: the
    settlement request has no field for it.
    """

    invoice_id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    currency: str
    invoice_status: InvoiceStatus
    status: SettlementStatus

    treatment_total: Decimal
    paid: Decimal
    paid_cash: Decimal
    paid_card: Decimal
    paid_other: Decimal
    remaining: Decimal
    advances_available: Decimal
    #: The most this invoice can still take. Zero once it is settled, which
    #: is what the panel disables its inputs on.
    max_payable: Decimal
    written_off: Decimal

    accepts_payment: bool
    #: None means no drawer is open, so the cash field is unavailable. Card
    #: and advances are unaffected.
    open_cash_session_id: uuid.UUID | None


class SettlementRequest(BaseModel, MoneyMixin):
    """What the patient is handing over, and nothing else.

    There is no `remaining` field, and that is the point: the backend
    computes it under the invoice's row lock and refuses anything larger.
    A client that disabled its inputs and a client that did not get the same
    answer.
    """

    cash: Decimal = Field(default=Decimal("0"), ge=0)
    card: Decimal = Field(default=Decimal("0"), ge=0)
    #: How much of the patient's existing advance to apply to this bill.
    advances: Decimal = Field(default=Decimal("0"), ge=0)

    received_on: date | None = None
    #: Which drawer the cash went into. Omitted uses the open session, and
    #: the request is refused if none is open.
    cash_session_id: uuid.UUID | None = None
    card_reference: str | None = Field(default=None, max_length=128)

    #: Client-generated, stable across retries of the same submission. A
    #: second request carrying the same key returns the first one's outcome
    #: instead of taking the money again, which is what makes a double-click
    #: harmless.
    request_key: str = Field(min_length=8, max_length=64)

    @field_validator("cash", "card", "advances", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        return _reject_float(value)

    @model_validator(mode="after")
    def _something_to_take(self) -> SettlementRequest:
        if self.cash + self.card + self.advances <= 0:
            raise ValueError("Enter an amount in cash, in card, or an advance to apply")
        return self


class SettlementResult(BaseModel, MoneyMixin):
    """What one submission did, and where the bill stands after it."""

    view: SettlementView
    applied_advances: Decimal
    cash_payment_id: uuid.UUID | None
    card_payment_id: uuid.UUID | None
    #: True when this submission had already been carried out: the money was
    #: taken once, and this is that outcome reported again.
    replayed: bool


class SettlementHistoryRow(BaseModel, MoneyMixin):
    """One allocation against the bill, reversals included.

    A reversed row is shown, not hidden. The difference between a correction
    and a figure that quietly changed is whether the trail still shows both.
    """

    allocation_id: uuid.UUID
    payment_id: uuid.UUID
    payment_number: str
    method: PaymentMethod
    amount: Decimal
    received_on: date
    reversed_at: datetime | None
    reversal_reason: str | None
