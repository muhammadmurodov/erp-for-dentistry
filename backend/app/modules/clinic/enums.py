"""Enumerations for the clinic: visits, encounters and treatment items."""

from __future__ import annotations

from enum import StrEnum


class AppointmentStatus(StrEnum):
    """Where a booking stands.

    Note what is **not** here: anything financial. An appointment is a
    promise about time. It produces revenue only if work is done and billed,
    and the clinic's own register shows plenty of attendances that produce
    none — a consultation, a check of earlier work, a patient sent away.
    """

    SCHEDULED = "SCHEDULED"
    CONFIRMED = "CONFIRMED"
    ARRIVED = "ARRIVED"
    """The patient is here. An encounter can be opened."""

    COMPLETED = "COMPLETED"
    """Seen. Says nothing about whether anything was charged."""

    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class EncounterStatus(StrEnum):
    """One visit to the chair."""

    OPEN = "OPEN"
    """In progress. Items may still be added."""

    COMPLETED = "COMPLETED"
    """The visit ended. Completed items become billable."""

    CANCELLED = "CANCELLED"
    """Abandoned — the patient left, or it was opened in error."""


class TreatmentItemStatus(StrEnum):
    """What has happened to one piece of work on one tooth.

    The clinic's paper register shows the same tooth carried across
    consecutive days — a canal one day, a post and an impression the next —
    so an item has to be able to outlive the visit that began it.
    """

    PLANNED = "PLANNED"
    """Agreed but not started. Carries forward to a later visit."""

    IN_PROGRESS = "IN_PROGRESS"
    """Begun and not finished. Carries forward; not billable."""

    COMPLETED = "COMPLETED"
    """Finished. **The only status that may be billed.**"""

    CANCELLED = "CANCELLED"
    """Abandoned. Never billable."""

    CORRECTED = "CORRECTED"
    """Superseded by another item, which records what really happened.
    Kept so the clinical history shows the correction rather than hiding it."""


#: The statuses from which an item may still move. A completed item is
#: changed by correcting it, not by editing it.
OPEN_ITEM_STATUSES = frozenset({TreatmentItemStatus.PLANNED, TreatmentItemStatus.IN_PROGRESS})


class PlanStatus(StrEnum):
    """A course of treatment, usually spanning several visits."""

    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    """Shown to the patient, awaiting their agreement."""

    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class DuplicateReviewStatus(StrEnum):
    """A suspected duplicate patient, awaiting a human decision.

    The clinic has never had a patient number: the paper register identifies
    people by name and birth year alone, and the same person appears on
    different days as unrelated rows. Duplicates are therefore expected, and
    merging two records is a decision somebody makes and signs, never
    something matching does on its own.
    """

    PENDING = "PENDING"
    MERGED = "MERGED"
    """Confirmed the same person; one record now points at the other."""

    DISTINCT = "DISTINCT"
    """Confirmed different people. Recorded so it is not raised again."""


#: Valid FDI tooth numbers. Permanent teeth are 11-18, 21-28, 31-38, 41-48;
#: deciduous teeth are 51-55, 61-65, 71-75, 81-85. Every tooth number in the
#: clinic's paper register falls in the permanent ranges.
PERMANENT_TEETH = frozenset(
    quadrant * 10 + position for quadrant in (1, 2, 3, 4) for position in range(1, 9)
)
DECIDUOUS_TEETH = frozenset(
    quadrant * 10 + position for quadrant in (5, 6, 7, 8) for position in range(1, 6)
)
VALID_TEETH = PERMANENT_TEETH | DECIDUOUS_TEETH

#: The SQL form of the same rule, for the check constraint. Kept next to the
#: Python so the two cannot drift apart unnoticed.
TOOTH_NUMBER_SQL = (
    "tooth_number IS NULL OR ("
    "mod(tooth_number, 10) BETWEEN 1 AND 8 AND (tooth_number / 10) BETWEEN 1 AND 4"
    ") OR ("
    "mod(tooth_number, 10) BETWEEN 1 AND 5 AND (tooth_number / 10) BETWEEN 5 AND 8"
    ")"
)


class SettlementStatus(StrEnum):
    """How far a patient's bill has been paid.

    Derived from the invoice total and its live allocations, every time it is
    asked for. It is deliberately **not** a column: a stored status is a
    second opinion about how much a patient has paid, and the moment it
    disagrees with the allocation rows somebody has to decide which is true.
    """

    UNPAID = "UNPAID"
    """Issued, nothing applied. To'lanmagan."""

    PARTIALLY_PAID = "PARTIALLY_PAID"
    """Some applied, a balance remains. Qisman to'langan — the remainder is
    the patient's qarzdorlik."""

    FULLY_PAID = "FULLY_PAID"
    """Settled in full. To'liq to'langan. No further payment is accepted."""

    WRITTEN_OFF = "WRITTEN_OFF"
    """Given up on. The revenue stands; the receivable does not."""

    NOT_PAYABLE = "NOT_PAYABLE"
    """A draft, a cancelled or a voided invoice. There is nothing to pay
    because there is no receivable — not the same thing as being paid."""
