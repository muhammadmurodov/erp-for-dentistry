"""Registering patients, finding them again, and deciding who is who.

The clinic has never had a patient number. Its paper register identifies
people by name and birth year, restarts its row numbers every day, and leaves
the birth year blank often enough that even that is not dependable. So the
two hard problems here are both about identity: recognising that somebody who
walks in today is the person who was treated last week, and refusing to
decide that automatically when it is genuinely ambiguous.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.clinic.enums import DuplicateReviewStatus
from app.modules.clinic.models import Patient, PatientDuplicateReview
from app.modules.sales.services.catalogue import CatalogueService

#: Uzbekistan: country code 998, then a two-digit operator code and seven
#: digits. A number given locally omits the country code.
UZ_COUNTRY_CODE = "998"
UZ_NATIONAL_LENGTH = 9
UZ_FULL_LENGTH = 12

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str | None) -> str | None:
    """Reduce a telephone number to digits that can be compared.

    Uzbek numbers are written every way imaginable — `+998 90 123-45-67`,
    `(90) 1234567`, `901234567` — and all of them mean the same person. A
    local nine-digit number is expanded to its full international form so
    that two spellings of one number match.

    Anything that is not recognisably Uzbek is kept as its digits, because
    patients do have foreign numbers and refusing them would simply mean the
    number is not recorded at all.
    """
    if raw is None:
        return None
    digits = _NON_DIGITS.sub("", raw)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == UZ_NATIONAL_LENGTH:
        return UZ_COUNTRY_CODE + digits
    # A trunk-prefixed local number: 8 then the nine national digits.
    if len(digits) == UZ_NATIONAL_LENGTH + 1 and digits.startswith("8"):
        return UZ_COUNTRY_CODE + digits[1:]
    return digits[:20]


#: Fewer digits than this is not worth matching a telephone number on: four
#: is the tail a receptionist reads back to a patient ("…45 67"), and three
#: would match most of the register.
PHONE_SEARCH_MIN_DIGITS = 4


def looks_like_a_phone_number(raw: str | None) -> bool:
    """Is this search term a telephone number rather than a name or a code?

    Only digits and the punctuation numbers are written with. A single letter
    makes it a name — `Karimova031930` is not a telephone number, and treating
    its digits as one matched other patients by the tail of their number.

    The minimum length matters as much as the alphabet: a two-digit query
    would match almost every patient on file, burying the name results the
    person was actually looking for.
    """
    if raw is None:
        return False
    term = raw.strip()
    if not term:
        return False
    if any(character.isalpha() for character in term):
        return False
    digits = _NON_DIGITS.sub("", term)
    return len(digits) >= PHONE_SEARCH_MIN_DIGITS


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def _require(value: str | None, field: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        raise ValidationFailed(f"{field} is required", details={"field": field})
    return cleaned


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A patient who may already be this patient, and why we think so."""

    patient_id: uuid.UUID
    code: str
    display_name: str
    birth_year: int | None
    phone: str | None
    reason: str


class PatientService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ------------------------------------------------------------ registering

    async def register(
        self,
        *,
        organization_id: uuid.UUID,
        family_name: str,
        given_name: str,
        patronymic: str | None = None,
        birth_year: int | None = None,
        birth_date: date | None = None,
        phone: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        assigned_dentist_user_id: uuid.UUID | None = None,
        registered_on: date | None = None,
        allow_duplicate: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> tuple[Patient, list[DuplicateCandidate]]:
        """Register a patient, raising a review if they look like a duplicate.

        Returns the patient and whatever candidates were found. Registration
        is never blocked: a receptionist with a patient in front of them must
        be able to finish, and a wrong refusal costs more than a review. The
        candidates are recorded as pending reviews for somebody to settle.
        """
        family_name = _require(family_name, "family_name")
        given_name = _require(given_name, "given_name")
        patronymic = _clean(patronymic)

        if birth_date is not None:
            if birth_year is not None and birth_date.year != birth_year:
                raise ValidationFailed(
                    "The birth date and birth year disagree",
                    details={"birth_date": birth_date.isoformat(), "birth_year": birth_year},
                )
            birth_year = birth_date.year
        if birth_year is not None and not 1900 <= birth_year <= date.today().year:
            raise ValidationFailed(
                "That birth year is not plausible", details={"birth_year": birth_year}
            )

        normalized = normalize_phone(phone)
        candidates = await self.find_duplicates(
            organization_id=organization_id,
            family_name=family_name,
            given_name=given_name,
            birth_year=birth_year,
            phone_normalized=normalized,
        )
        if candidates and not allow_duplicate:
            # Not an error — see the docstring. The caller is told, and the
            # reviews below make sure it is not quietly forgotten.
            pass

        display_name = " ".join(p for p in (family_name, given_name, patronymic) if p)
        patient = Patient(
            organization_id=organization_id,
            code=await next_number(
                self._session,
                organization_id=organization_id,
                name="patient",
                prefix="P-",
                padding=5,
            ),
            family_name=family_name,
            given_name=given_name,
            patronymic=patronymic,
            display_name=display_name,
            birth_year=birth_year,
            birth_date=birth_date,
            phone=_clean(phone),
            phone_normalized=normalized,
            address=_clean(address),
            notes=_clean(notes),
            registered_on=registered_on or date.today(),
            assigned_dentist_user_id=assigned_dentist_user_id,
        )
        self._session.add(patient)
        await self._session.flush()

        # The patient is also the party that gets billed, in the ordinary
        # case. Creating the customer here keeps the two in step without the
        # clinic module ever writing to a sales table itself.
        customer = await CatalogueService(self._session, self._audit).create_customer(
            organization_id=organization_id,
            code=patient.code,
            display_name=display_name,
            phone=_clean(phone),
        )
        patient.customer_id = customer.id
        await self._session.flush()

        for candidate in candidates:
            await self._raise_review(
                organization_id=organization_id,
                patient_id=patient.id,
                candidate_id=candidate.patient_id,
                reason=candidate.reason,
            )

        self._audit.record(
            events.PATIENT_REGISTERED,
            organization_id=organization_id,
            target_type="patient",
            target_id=patient.id,
            target_label=patient.code,
            actor_user_id=actor_user_id,
            new_values={
                "code": patient.code,
                # The name is the patient's own data; the audit recorder
                # redacts what must not be kept, and the label carries the
                # code so a reader can find the record without it.
                "birth_year": birth_year,
                "duplicate_candidates": len(candidates),
            },
        )
        return patient, candidates

    async def update(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        **fields: object,
    ) -> Patient:
        """Edit a patient's registration details."""
        patient = await self.get(organization_id=organization_id, patient_id=patient_id)
        if patient.merged_into_patient_id is not None:
            raise Conflict(
                "This record has been merged into another and cannot be edited",
                details={"merged_into": str(patient.merged_into_patient_id)},
            )

        before = {
            "family_name": patient.family_name,
            "given_name": patient.given_name,
            "phone": patient.phone,
            "birth_year": patient.birth_year,
        }
        for key in ("family_name", "given_name"):
            if key in fields:
                setattr(patient, key, _require(fields[key], key))  # type: ignore[arg-type]
        for key in ("patronymic", "address", "notes"):
            if key in fields:
                setattr(patient, key, _clean(fields[key]))  # type: ignore[arg-type]
        if "phone" in fields:
            patient.phone = _clean(fields["phone"])  # type: ignore[arg-type]
            patient.phone_normalized = normalize_phone(patient.phone)
        if "birth_year" in fields:
            patient.birth_year = fields["birth_year"]  # type: ignore[assignment]
        if "birth_date" in fields:
            patient.birth_date = fields["birth_date"]  # type: ignore[assignment]
            if patient.birth_date is not None:
                patient.birth_year = patient.birth_date.year
        if "assigned_dentist_user_id" in fields:
            patient.assigned_dentist_user_id = fields["assigned_dentist_user_id"]  # type: ignore[assignment]
        if "is_active" in fields:
            patient.is_active = bool(fields["is_active"])

        patient.display_name = " ".join(
            p for p in (patient.family_name, patient.given_name, patient.patronymic) if p
        )
        await self._session.flush()

        self._audit.record(
            events.PATIENT_UPDATED,
            organization_id=organization_id,
            target_type="patient",
            target_id=patient.id,
            target_label=patient.code,
            actor_user_id=actor_user_id,
            old_values=before,
            new_values={
                "family_name": patient.family_name,
                "given_name": patient.given_name,
                "phone": patient.phone,
                "birth_year": patient.birth_year,
            },
        )
        return patient

    # -------------------------------------------------------------- duplicates

    async def find_duplicates(
        self,
        *,
        organization_id: uuid.UUID,
        family_name: str,
        given_name: str,
        birth_year: int | None,
        phone_normalized: str | None,
        exclude_patient_id: uuid.UUID | None = None,
    ) -> list[DuplicateCandidate]:
        """Who already on file might be this person.

        Two signals, both weak on their own and deliberately so:

        * the same telephone number — but relatives share one, so this alone
          is a prompt, never a verdict;
        * the same family and given name — and if a birth year is known on
          both sides and differs, that is enough to rule the match out, which
          is what keeps siblings apart.
        """
        conditions = []
        if phone_normalized:
            conditions.append(Patient.phone_normalized == phone_normalized)
        conditions.append(
            func.lower(Patient.family_name) == family_name.lower(),
        )
        stmt = select(Patient).where(
            Patient.organization_id == organization_id,
            Patient.merged_into_patient_id.is_(None),
            or_(*conditions),
        )
        if exclude_patient_id is not None:
            stmt = stmt.where(Patient.id != exclude_patient_id)

        found: list[DuplicateCandidate] = []
        for other in (await self._session.execute(stmt)).scalars():
            same_phone = bool(phone_normalized) and other.phone_normalized == phone_normalized
            same_name = (
                other.family_name.lower() == family_name.lower()
                and other.given_name.lower() == given_name.lower()
            )
            years_known = birth_year is not None and other.birth_year is not None
            years_differ = years_known and other.birth_year != birth_year

            if same_name and not years_differ:
                reason = (
                    "Same name and birth year"
                    if years_known
                    else "Same name; no birth year to tell them apart"
                )
            elif same_phone and not years_differ:
                reason = "Same telephone number — may be a relative"
            else:
                continue
            found.append(
                DuplicateCandidate(
                    patient_id=other.id,
                    code=other.code,
                    display_name=other.display_name,
                    birth_year=other.birth_year,
                    phone=other.phone,
                    reason=reason,
                )
            )
        return found

    async def _raise_review(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        candidate_id: uuid.UUID,
        reason: str,
    ) -> None:
        existing = (
            await self._session.execute(
                select(PatientDuplicateReview).where(
                    PatientDuplicateReview.organization_id == organization_id,
                    PatientDuplicateReview.patient_id == patient_id,
                    PatientDuplicateReview.candidate_patient_id == candidate_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            PatientDuplicateReview(
                organization_id=organization_id,
                patient_id=patient_id,
                candidate_patient_id=candidate_id,
                reason=reason,
                status=DuplicateReviewStatus.PENDING,
            )
        )
        await self._session.flush()

    async def list_reviews(
        self,
        *,
        organization_id: uuid.UUID,
        status: DuplicateReviewStatus | None = DuplicateReviewStatus.PENDING,
    ) -> list[PatientDuplicateReview]:
        stmt = select(PatientDuplicateReview).where(
            PatientDuplicateReview.organization_id == organization_id
        )
        if status is not None:
            stmt = stmt.where(PatientDuplicateReview.status == status)
        return list((await self._session.execute(stmt)).scalars().all())

    async def review_queue(
        self,
        *,
        organization_id: uuid.UUID,
        status: DuplicateReviewStatus | None = DuplicateReviewStatus.PENDING,
    ) -> list[tuple[PatientDuplicateReview, Patient, Patient]]:
        """Reviews with both records attached, newest first.

        One query for the reviews and one for every patient they mention,
        rather than two per review: the queue is short in a clinic this size,
        but a list endpoint that fans out per row is how a short list becomes
        a slow screen as the years accumulate.

        A review whose patients have since been deleted is skipped rather than
        raised: nothing can be decided about records that are not there, and a
        queue that cannot be opened is worse than one missing a row.
        """
        reviews = await self.list_reviews(organization_id=organization_id, status=status)
        if not reviews:
            return []
        wanted = {r.patient_id for r in reviews} | {r.candidate_patient_id for r in reviews}
        rows = (
            await self._session.execute(
                select(Patient).where(
                    Patient.organization_id == organization_id,
                    Patient.id.in_(wanted),
                )
            )
        ).scalars()
        by_id = {p.id: p for p in rows}
        queue: list[tuple[PatientDuplicateReview, Patient, Patient]] = []
        for review in reviews:
            patient = by_id.get(review.patient_id)
            candidate = by_id.get(review.candidate_patient_id)
            if patient is None or candidate is None:
                continue
            queue.append((review, patient, candidate))
        queue.sort(key=lambda item: item[0].created_at, reverse=True)
        return queue

    async def resolve_review(
        self,
        *,
        organization_id: uuid.UUID,
        review_id: uuid.UUID,
        merge: bool,
        note: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> PatientDuplicateReview:
        """Settle a suspected duplicate, one way or the other.

        A note is required either way. "Not a duplicate" is as much of a
        decision as a merge, and the next receptionist to see these two
        records deserves to know somebody already looked.
        """
        if not note or not note.strip():
            raise ValidationFailed("A decision about a duplicate needs a note")

        review = (
            await self._session.execute(
                select(PatientDuplicateReview).where(
                    PatientDuplicateReview.organization_id == organization_id,
                    PatientDuplicateReview.id == review_id,
                )
            )
        ).scalar_one_or_none()
        if review is None:
            raise NotFound("Duplicate review not found")
        if review.status is not DuplicateReviewStatus.PENDING:
            raise Conflict("This duplicate review has already been decided")

        if merge:
            await self.merge(
                organization_id=organization_id,
                patient_id=review.patient_id,
                into_patient_id=review.candidate_patient_id,
                reason=note.strip(),
                actor_user_id=actor_user_id,
            )
        review.status = DuplicateReviewStatus.MERGED if merge else DuplicateReviewStatus.DISTINCT
        review.decided_at = datetime.now(UTC)
        review.decided_by_user_id = actor_user_id
        review.decision_note = note.strip()
        await self._session.flush()
        return review

    async def merge(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        into_patient_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> Patient:
        """Declare that two records are one person.

        The losing record is kept and marked, never deleted: treatments were
        recorded against it, and an audit trail that cannot say where they
        went is no audit trail. Clinical records are **not** moved either —
        they stay attached to the encounter that produced them, and the
        surviving record gathers them through the merge link.
        """
        if not reason or not reason.strip():
            raise ValidationFailed("A merge requires a reason")
        if patient_id == into_patient_id:
            raise ValidationFailed("A patient cannot be merged into themselves")

        losing = await self.get(organization_id=organization_id, patient_id=patient_id)
        surviving = await self.get(organization_id=organization_id, patient_id=into_patient_id)
        if losing.merged_into_patient_id is not None:
            raise Conflict("That record has already been merged")
        if surviving.merged_into_patient_id is not None:
            raise Conflict("Cannot merge into a record that has itself been merged")

        losing.merged_into_patient_id = surviving.id
        losing.merged_at = datetime.now(UTC)
        losing.merged_by_user_id = actor_user_id
        losing.is_active = False
        await self._session.flush()

        self._audit.record(
            events.PATIENT_MERGED,
            organization_id=organization_id,
            target_type="patient",
            target_id=losing.id,
            target_label=losing.code,
            reason=reason.strip(),
            actor_user_id=actor_user_id,
            old_values={"code": losing.code},
            new_values={"merged_into": surviving.code},
        )
        return surviving

    # ------------------------------------------------------------------ reads

    async def get(self, *, organization_id: uuid.UUID, patient_id: uuid.UUID) -> Patient:
        patient = (
            await self._session.execute(
                select(Patient).where(
                    Patient.organization_id == organization_id, Patient.id == patient_id
                )
            )
        ).scalar_one_or_none()
        if patient is None:
            raise NotFound("Patient not found")
        return patient

    async def search(
        self,
        *,
        organization_id: uuid.UUID,
        query: str | None = None,
        include_merged: bool = False,
        limit: int = 50,
    ) -> list[Patient]:
        """Find a patient by name, code or telephone number.

        The query is matched against the normalised telephone number as well
        as the name, so `90 123 45 67` finds a patient recorded as
        `+998901234567` — but only when the query *is* a telephone number.
        A term with letters in it is a name or a code, and matching its stray
        digits against everybody's telephone number returned strangers:
        searching the code `P-00015` once produced five unrelated patients,
        because `00015` is a substring of their numbers. Showing one patient's
        record to somebody looking for another is not a cosmetic fault.
        """
        stmt = select(Patient).where(Patient.organization_id == organization_id)
        if not include_merged:
            stmt = stmt.where(Patient.merged_into_patient_id.is_(None))
        if query and query.strip():
            text_term = f"%{query.strip().lower()}%"
            conditions = [
                func.lower(Patient.display_name).like(text_term),
                func.lower(Patient.code).like(text_term),
            ]
            phone_term = normalize_phone(query) if looks_like_a_phone_number(query) else None
            if phone_term:
                conditions.append(Patient.phone_normalized.like(f"%{phone_term}%"))
            stmt = stmt.where(or_(*conditions))
        stmt = stmt.order_by(Patient.family_name, Patient.given_name).limit(min(limit, 200))
        return list((await self._session.execute(stmt)).scalars().all())
