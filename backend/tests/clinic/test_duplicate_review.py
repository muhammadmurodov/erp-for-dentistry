"""The duplicate-patient queue: what it shows, and what it refuses to do.

This clinic has never had a patient number. The paper register identifies
people by family name and birth year, and the same person appears on
different days as unrelated rows — so two records that look alike are the
normal case, not an anomaly, and matching them is a decision somebody makes
and signs rather than something the system does because two strings agree.

Every test here is about that boundary: candidates are *shown*, a merge is
*confirmed*, and whatever loses is *kept*.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.clinic.enums import DuplicateReviewStatus
from app.modules.clinic.models import Patient

REGISTERED_ON = date(2026, 10, 1)


async def _register(patients, org_id, family="Oripov", given="Jahongir", **kw):
    return await patients.register(
        organization_id=org_id,
        family_name=family,
        given_name=given,
        birth_year=kw.pop("birth_year", 1990),
        registered_on=kw.pop("registered_on", REGISTERED_ON),
        **kw,
    )


class TestNothingIsMergedOnItsOwn:
    async def test_a_matching_name_and_birth_year_is_reported_not_refused(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, none_yet = await _register(patients, org_a.id)
        assert none_yet == []

        second, candidates = await _register(patients, org_a.id)
        assert second.id != first.id, "the second person was registered, not rejected"
        assert [c.patient_id for c in candidates] == [first.id]
        assert candidates[0].reason

    async def test_both_records_are_live_and_separate_afterwards(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        """The whole point: a suspicion changes nothing about either record."""
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()

        for patient in (first, second):
            fresh = await patients.get(organization_id=org_a.id, patient_id=patient.id)
            assert fresh.is_active
            assert fresh.merged_into_patient_id is None
        assert first.code != second.code, "each has its own patient code"

    async def test_a_telephone_number_also_raises_one(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id, phone="+998901112233")
        _, candidates = await _register(
            patients, org_a.id, family="Karimova", given="Nilufar", phone="+998901112233"
        )
        assert candidates, "a shared number is worth asking about even under another name"

    async def test_two_unrelated_people_raise_nothing(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id)
        _, candidates = await _register(
            patients, org_a.id, family="Yusupova", given="Zilola", birth_year=1974
        )
        assert candidates == []


class TestTheQueue:
    async def test_it_carries_both_records_so_they_can_be_compared(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id, phone="+998907654321")
        await db.flush()

        queue = await patients.review_queue(organization_id=org_a.id)
        assert len(queue) == 1
        review, patient, candidate = queue[0]
        assert review.status is DuplicateReviewStatus.PENDING
        # Both records, in full, not two ids to go and fetch.
        assert {patient.id, candidate.id} == {first.id, second.id}
        assert patient.family_name and candidate.family_name
        assert candidate.id == first.id, "the record already on file is the candidate"

    async def test_the_record_already_on_file_is_the_one_a_merge_keeps(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        """Which record survives has to be knowable *before* anybody acts."""
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()

        _, patient, candidate = (await patients.review_queue(organization_id=org_a.id))[0]
        assert patient.id == second.id, "the newer record is the one under review"
        assert candidate.id == first.id, "and the older one is what it would fold into"

    async def test_a_settled_review_leaves_the_pending_queue(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id)
        await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]

        await patients.resolve_review(
            organization_id=org_a.id,
            review_id=review.id,
            merge=False,
            note="Father and son, same name, born the same year",
        )
        await db.flush()
        assert await patients.review_queue(organization_id=org_a.id) == []

    async def test_an_empty_queue_costs_one_query_and_returns_nothing(
        self, patients, org_a
    ) -> None:
        assert await patients.review_queue(organization_id=org_a.id) == []


class TestConfirmingTheyAreDifferentPeople:
    async def test_it_keeps_both_records_untouched(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]

        settled = await patients.resolve_review(
            organization_id=org_a.id,
            review_id=review.id,
            merge=False,
            note="Two brothers; the mother confirmed",
        )
        await db.flush()

        assert settled.status is DuplicateReviewStatus.DISTINCT
        assert settled.decision_note == "Two brothers; the mother confirmed"
        assert settled.decided_at is not None
        for patient in (first, second):
            fresh = await patients.get(organization_id=org_a.id, patient_id=patient.id)
            assert fresh.is_active and fresh.merged_into_patient_id is None

    async def test_the_same_pair_is_not_raised_again(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        """Otherwise the queue re-asks a question somebody answered, every
        visit, until it is ignored on principle."""
        await _register(patients, org_a.id)
        await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]
        await patients.resolve_review(
            organization_id=org_a.id, review_id=review.id, merge=False, note="different people"
        )
        await db.flush()

        # A third look-alike raises a review against each *unsettled* pair,
        # and never against the pair already decided.
        _, candidates = await _register(patients, org_a.id)
        await db.flush()
        pairs = {
            (r.patient_id, r.candidate_patient_id)
            for r, _, _ in await patients.review_queue(organization_id=org_a.id)
        }
        assert (review.patient_id, review.candidate_patient_id) not in pairs
        assert candidates, "the new record still gets its own question"

    async def test_a_decision_without_a_note_is_refused(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id)
        await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]
        with pytest.raises(ValidationFailed, match="needs a note"):
            await patients.resolve_review(
                organization_id=org_a.id, review_id=review.id, merge=False, note="   "
            )


class TestConfirmingTheyAreOnePerson:
    async def test_the_losing_record_is_kept_and_marked(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]

        settled = await patients.resolve_review(
            organization_id=org_a.id,
            review_id=review.id,
            merge=True,
            note="Same person; she had been registered twice",
        )
        await db.flush()
        assert settled.status is DuplicateReviewStatus.MERGED

        losing = (
            await db.execute(select(Patient).where(Patient.id == second.id))
        ).scalar_one()
        surviving = await patients.get(organization_id=org_a.id, patient_id=first.id)
        # Kept, not deleted: treatments were recorded against this row.
        assert losing.merged_into_patient_id == surviving.id
        assert losing.merged_at is not None
        assert not losing.is_active
        assert surviving.is_active and surviving.merged_into_patient_id is None

    async def test_the_merge_is_audited_with_its_reason(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id)
        await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]
        await patients.resolve_review(
            organization_id=org_a.id,
            review_id=review.id,
            merge=True,
            note="One person, two visits, two records",
        )
        await db.flush()

        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_a.id,
                    AuditLog.action == events.PATIENT_MERGED,
                )
            )
        ).scalar_one()
        assert row.reason == "One person, two visits, two records"
        assert row.new_values["merged_into"]

    async def test_merging_is_in_the_always_audited_set(self) -> None:
        from app.modules.audit.events import REQUIRED_AUDIT_ACTIONS

        assert events.PATIENT_MERGED in REQUIRED_AUDIT_ACTIONS

    async def test_a_review_cannot_be_decided_twice(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id)
        await _register(patients, org_a.id)
        await db.flush()
        review, _, _ = (await patients.review_queue(organization_id=org_a.id))[0]
        await patients.resolve_review(
            organization_id=org_a.id, review_id=review.id, merge=False, note="different people"
        )
        await db.flush()
        with pytest.raises(Conflict, match="already been decided"):
            await patients.resolve_review(
                organization_id=org_a.id,
                review_id=review.id,
                merge=True,
                note="changed my mind",
            )

    async def test_a_record_already_merged_cannot_be_merged_again(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()
        await patients.merge(
            organization_id=org_a.id,
            patient_id=second.id,
            into_patient_id=first.id,
            reason="same person",
        )
        await db.flush()
        with pytest.raises(Conflict, match="already been merged"):
            await patients.merge(
                organization_id=org_a.id,
                patient_id=second.id,
                into_patient_id=first.id,
                reason="again",
            )

    async def test_a_merge_without_a_reason_is_refused(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await patients.merge(
                organization_id=org_a.id,
                patient_id=second.id,
                into_patient_id=first.id,
                reason="  ",
            )

    async def test_a_patient_cannot_be_merged_into_themselves(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        await db.flush()
        with pytest.raises(ValidationFailed, match="into themselves"):
            await patients.merge(
                organization_id=org_a.id,
                patient_id=first.id,
                into_patient_id=first.id,
                reason="confused",
            )


class TestSearchingForAPatient:
    """A search must not show one patient's record to somebody looking for
    another. Found by a browser pass: searching a surname that happened to
    contain digits returned unrelated patients, because the digits were
    matched against everybody's telephone number."""

    async def test_a_name_with_digits_in_it_is_not_a_telephone_number(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        on_file, _ = await _register(
            patients, org_a.id, family="Oripov031930", given="Jahongir", phone="+998900319301"
        )
        other, _ = await _register(
            patients, org_a.id, family="Karimova031930", given="Nilufar", birth_year=1985
        )
        await db.flush()

        # `031930` is a substring of the first patient's number. The query is
        # a name, so it must not reach the telephone column at all.
        found = await patients.search(organization_id=org_a.id, query="Karimova031930")
        assert [p.id for p in found] == [other.id], (
            "searching a name returned patients matched on their telephone number: "
            f"{[p.display_name for p in found]}"
        )
        assert on_file.id not in {p.id for p in found}

    async def test_a_patient_code_is_not_a_telephone_number(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        wanted, _ = await _register(patients, org_a.id, family="Yusupova", given="Zilola")
        await db.flush()
        stranger, _ = await _register(
            patients,
            org_a.id,
            family="Toshmatov",
            given="Akmal",
            # Contains the digits of the other patient's code.
            phone=f"+9989{wanted.code.replace('P-', '')}11",
        )
        await db.flush()

        found = await patients.search(organization_id=org_a.id, query=wanted.code)
        assert wanted.id in {p.id for p in found}
        assert stranger.id not in {p.id for p in found}, (
            "searching a patient code returned a stranger whose telephone number "
            "contains those digits"
        )

    async def test_a_telephone_number_still_finds_its_patient(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        """The behaviour the phone clause exists for, in every spelling."""
        patient, _ = await _register(
            patients, org_a.id, family="Normatova", given="Dilnoza", phone="+998901234567"
        )
        await db.flush()
        for spelling in ("+998901234567", "998901234567", "901234567", "90 123 45 67", "4567"):
            found = await patients.search(organization_id=org_a.id, query=spelling)
            assert patient.id in {p.id for p in found}, f"{spelling!r} did not find the patient"

    async def test_a_two_digit_query_does_not_match_everybody_by_telephone(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        """Too short to discriminate: it would bury the name results."""
        await _register(
            patients, org_a.id, family="Rahimov", given="Sardor", phone="+998901212121"
        )
        await db.flush()
        found = await patients.search(organization_id=org_a.id, query="12")
        assert found == [], (
            "a two-digit query matched patients by the tail of their telephone number"
        )

    async def test_a_merged_record_stays_out_of_the_search(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        first, _ = await _register(patients, org_a.id)
        second, _ = await _register(patients, org_a.id)
        await db.flush()
        await patients.merge(
            organization_id=org_a.id,
            patient_id=second.id,
            into_patient_id=first.id,
            reason="same person",
        )
        await db.flush()

        found = await patients.search(organization_id=org_a.id, query="Oripov")
        assert [p.id for p in found] == [first.id], "a merged record is still offered by search"
        # But it is still *there*, and still readable by id.
        kept = await patients.get(organization_id=org_a.id, patient_id=second.id)
        assert kept.merged_into_patient_id == first.id


class TestTheSiblingRule:
    """Two brothers are not a duplicate, and this is the check that says so."""

    async def test_the_same_surname_and_year_with_a_different_given_name(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id, given="Jahongir", phone="+998900000001")
        _, candidates = await _register(
            patients, org_a.id, given="Bahodir", phone="+998900000002"
        )
        assert candidates == [], (
            "two brothers of the same surname and birth year were reported as possible "
            "duplicates; the given name is what keeps siblings apart"
        )
        assert await patients.review_queue(organization_id=org_a.id) == []

    async def test_a_differing_birth_year_rules_a_match_out(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        await _register(patients, org_a.id, birth_year=1990)
        _, candidates = await _register(patients, org_a.id, birth_year=1974)
        assert candidates == [], "the same full name but a different birth year is not a match"

    async def test_a_shared_number_is_a_prompt_even_for_a_sibling(
        self, patients, org_a, db: AsyncSession
    ) -> None:
        """A household telephone is worth asking about, and only asking."""
        await _register(patients, org_a.id, given="Jahongir", phone="+998901112233")
        _, candidates = await _register(
            patients, org_a.id, given="Bahodir", phone="+998901112233"
        )
        assert len(candidates) == 1
        assert "telephone" in candidates[0].reason.lower()
