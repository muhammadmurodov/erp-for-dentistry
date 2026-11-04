"""Booking patients in.

An appointment is a promise about time and nothing else: it moves no money,
recognises no revenue and creates no invoice. What it has to get right is the
time — which is the clinic's own, not the server's — and the fact that one
dentist cannot be in two chairs at once.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.modules.clinic.enums import AppointmentStatus

TUESDAY = date(2026, 10, 6)
WEDNESDAY = date(2026, 10, 7)


async def a_patient(patients, sales, *, family="Bookerov", given="Ali", dentist=None):
    patient, _candidates = await patients.register(
        organization_id=sales.id,
        family_name=family,
        given_name=given,
        birth_year=1990,
        assigned_dentist_user_id=dentist,
    )
    return patient


class TestBooking:
    async def test_a_booking_carries_the_day_and_the_hour(
        self, treatments, patients, sales, db: AsyncSession
    ) -> None:
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id,
            patient_id=patient.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            reason="Pain in the lower left",
        )
        await db.flush()
        assert appointment.scheduled_on == TUESDAY
        assert appointment.scheduled_at == time(15, 0)
        assert appointment.duration_minutes == 30
        assert appointment.status is AppointmentStatus.SCHEDULED
        assert appointment.reason == "Pain in the lower left"

    async def test_a_booking_needs_no_hour(self, treatments, patients, sales) -> None:
        """The clinic's register books "Tuesday" as often as "Tuesday at three"."""
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY
        )
        assert appointment.scheduled_at is None

    async def test_it_inherits_the_patient_s_dentist(
        self, treatments, patients, sales, org_a
    ) -> None:
        patient = await a_patient(
            patients, sales, family="Assigned", given="Patient", dentist=org_a.owner.id
        )
        appointment = await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY
        )
        assert appointment.dentist_user_id == org_a.owner.id

    async def test_a_merged_record_cannot_be_booked_against(
        self, treatments, patients, sales
    ) -> None:
        keep = await a_patient(patients, sales, family="Keepov")
        gone = await a_patient(patients, sales, family="Goneov")
        await patients.merge(
            organization_id=sales.id,
            patient_id=gone.id,
            into_patient_id=keep.id,
            reason="The same person, registered twice",
        )
        with pytest.raises(Conflict, match="merged"):
            await treatments.book(
                organization_id=sales.id, patient_id=gone.id, scheduled_on=TUESDAY
            )


class TestOneDentistOneChair:
    async def test_an_overlapping_booking_is_refused(
        self, treatments, patients, sales, org_a
    ) -> None:
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        with pytest.raises(Conflict, match="already booked"):
            await treatments.book(
                organization_id=sales.id,
                patient_id=second.id,
                scheduled_on=TUESDAY,
                scheduled_at=time(15, 15),
                duration_minutes=30,
                dentist_user_id=org_a.owner.id,
            )

    async def test_back_to_back_bookings_are_allowed(
        self, treatments, patients, sales, org_a
    ) -> None:
        """Touching is not overlapping: that is how a day is filled."""
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        later = await treatments.book(
            organization_id=sales.id,
            patient_id=second.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 30),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        assert later.scheduled_at == time(15, 30)

    async def test_another_dentist_may_take_the_same_hour(
        self, treatments, patients, sales, org_a, db: AsyncSession
    ) -> None:
        from app.modules.audit.service import AuditRecorder
        from app.modules.identity.service import IdentityService

        identity = IdentityService(db, AuditRecorder(db, actor_label="test"))
        created = await identity.create_user_in_organization(
            organization_id=sales.id,
            email="second-dentist@example.com",
            full_name="Second Dentist",
            password="second-dentist-password-1",
            role_codes=["dentist"],
        )
        other_id = getattr(created, "id", None) or created[0].id
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        booked = await treatments.book(
            organization_id=sales.id,
            patient_id=second.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=other_id,
        )
        assert booked.scheduled_at == time(15, 0)

    async def test_the_same_hour_on_another_day_is_free(
        self, treatments, patients, sales, org_a
    ) -> None:
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        booked = await treatments.book(
            organization_id=sales.id,
            patient_id=second.id,
            scheduled_on=WEDNESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        assert booked.scheduled_on == WEDNESDAY

    async def test_a_cancelled_booking_frees_its_hour(
        self, treatments, patients, sales, org_a
    ) -> None:
        """A cancelled appointment is a record of a day, not a claim on it."""
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        taken = await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        await treatments.set_appointment_status(
            organization_id=sales.id,
            appointment_id=taken.id,
            status=AppointmentStatus.CANCELLED,
            reason="The patient rang to call it off",
        )
        booked = await treatments.book(
            organization_id=sales.id,
            patient_id=second.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        assert booked.scheduled_at == time(15, 0)

    async def test_a_booking_with_no_hour_blocks_nothing(
        self, treatments, patients, sales, org_a
    ) -> None:
        """Otherwise one vague booking would fill the whole day."""
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            dentist_user_id=org_a.owner.id,
        )
        booked = await treatments.book(
            organization_id=sales.id,
            patient_id=second.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        assert booked.scheduled_at == time(15, 0)


class TestMoving:
    async def test_a_booking_can_be_moved(self, treatments, patients, sales, org_a) -> None:
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id,
            patient_id=patient.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        moved = await treatments.reschedule(
            organization_id=sales.id,
            appointment_id=appointment.id,
            scheduled_on=WEDNESDAY,
            scheduled_at=time(11, 0),
            duration_minutes=45,
            reason="The patient asked for the morning",
        )
        assert moved.scheduled_on == WEDNESDAY
        assert moved.scheduled_at == time(11, 0)
        assert moved.duration_minutes == 45
        assert moved.status is AppointmentStatus.SCHEDULED

    async def test_moving_it_onto_another_booking_is_refused(
        self, treatments, patients, sales, org_a
    ) -> None:
        first = await a_patient(patients, sales, family="Firstov")
        second = await a_patient(patients, sales, family="Secondov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=first.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(9, 0),
            duration_minutes=60,
            dentist_user_id=org_a.owner.id,
        )
        moving = await treatments.book(
            organization_id=sales.id,
            patient_id=second.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        with pytest.raises(Conflict, match="already booked"):
            await treatments.reschedule(
                organization_id=sales.id,
                appointment_id=moving.id,
                scheduled_on=TUESDAY,
                scheduled_at=time(9, 30),
                duration_minutes=30,
            )

    async def test_moving_it_to_its_own_time_is_not_a_clash_with_itself(
        self, treatments, patients, sales, org_a
    ) -> None:
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id,
            patient_id=patient.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
            dentist_user_id=org_a.owner.id,
        )
        moved = await treatments.reschedule(
            organization_id=sales.id,
            appointment_id=appointment.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=60,
        )
        assert moved.duration_minutes == 60

    async def test_a_closed_booking_cannot_be_moved(self, treatments, patients, sales) -> None:
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY
        )
        await treatments.set_appointment_status(
            organization_id=sales.id,
            appointment_id=appointment.id,
            status=AppointmentStatus.NO_SHOW,
        )
        with pytest.raises(Conflict, match="cannot be"):
            await treatments.reschedule(
                organization_id=sales.id,
                appointment_id=appointment.id,
                scheduled_on=WEDNESDAY,
            )


class TestTheDay:
    async def test_cancelling_requires_a_reason(self, treatments, patients, sales) -> None:
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY
        )
        with pytest.raises(ValidationFailed, match="requires a reason"):
            await treatments.set_appointment_status(
                organization_id=sales.id,
                appointment_id=appointment.id,
                status=AppointmentStatus.CANCELLED,
            )

    async def test_a_finished_appointment_cannot_become_a_no_show(
        self, treatments, patients, sales
    ) -> None:
        """Somebody who was treated was not absent."""
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY
        )
        await treatments.set_appointment_status(
            organization_id=sales.id,
            appointment_id=appointment.id,
            status=AppointmentStatus.COMPLETED,
        )
        with pytest.raises(Conflict, match="closed"):
            await treatments.set_appointment_status(
                organization_id=sales.id,
                appointment_id=appointment.id,
                status=AppointmentStatus.NO_SHOW,
            )

    async def test_opening_a_visit_marks_the_patient_arrived(
        self, treatments, patients, sales
    ) -> None:
        patient = await a_patient(patients, sales)
        appointment = await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY
        )
        await treatments.open_encounter(
            organization_id=sales.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            occurred_on=TUESDAY,
        )
        assert appointment.status is AppointmentStatus.ARRIVED

    async def test_the_day_s_list_carries_the_names_and_the_visit(
        self, treatments, patients, sales, db: AsyncSession
    ) -> None:
        patient = await a_patient(patients, sales, family="Listov", given="Dilnoza")
        appointment = await treatments.book(
            organization_id=sales.id,
            patient_id=patient.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(9, 30),
            duration_minutes=30,
        )
        encounter = await treatments.open_encounter(
            organization_id=sales.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            occurred_on=TUESDAY,
        )
        await db.flush()

        rows = await treatments.appointments_for_the_day(organization_id=sales.id, on=TUESDAY)
        assert len(rows) == 1
        row = rows[0]
        assert row.patient_name == patient.display_name
        assert row.patient_code == patient.code
        assert row.encounter_id == encounter.id

    async def test_the_list_is_in_the_order_the_day_happens(
        self, treatments, patients, sales
    ) -> None:
        late = await a_patient(patients, sales, family="Lateov")
        early = await a_patient(patients, sales, family="Earlyov")
        vague = await a_patient(patients, sales, family="Vagueov")
        await treatments.book(
            organization_id=sales.id,
            patient_id=late.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(16, 0),
            duration_minutes=30,
        )
        await treatments.book(
            organization_id=sales.id,
            patient_id=early.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(9, 0),
            duration_minutes=30,
        )
        await treatments.book(organization_id=sales.id, patient_id=vague.id, scheduled_on=TUESDAY)
        rows = await treatments.appointments_for_the_day(organization_id=sales.id, on=TUESDAY)
        # No time means no hour claimed, so it sorts to the top of the day.
        assert [r.patient_code for r in rows] == [
            vague.code,
            early.code,
            late.code,
        ]

    async def test_a_patient_s_history_is_every_booking(self, treatments, patients, sales) -> None:
        patient = await a_patient(patients, sales)
        await treatments.book(organization_id=sales.id, patient_id=patient.id, scheduled_on=TUESDAY)
        await treatments.book(
            organization_id=sales.id, patient_id=patient.id, scheduled_on=WEDNESDAY
        )
        rows = await treatments.appointments_for_the_day(
            organization_id=sales.id, patient_id=patient.id
        )
        assert [r.appointment.scheduled_on for r in rows] == [TUESDAY, WEDNESDAY]

    async def test_a_booking_recognises_no_revenue(
        self, treatments, patients, sales, db: AsyncSession
    ) -> None:
        """An appointment is a promise about time and nothing else."""
        from app.modules.accounting.services.reports import ReportService

        patient = await a_patient(patients, sales)
        await treatments.book(
            organization_id=sales.id,
            patient_id=patient.id,
            scheduled_on=TUESDAY,
            scheduled_at=time(15, 0),
            duration_minutes=30,
        )
        await db.flush()
        report = await ReportService(db).trial_balance(
            organization_id=sales.id,
            from_date=date(2026, 1, 1),
            to_date=date(2026, 12, 31),
        )
        assert report.rows == [], "a booking is not an accounting event"


class TestWhoMayTakeABooking:
    """The roster the appointment form is drawn from.

    It exists because reception — the only people who book anything — may
    not read the staff directory, and a form that cannot name a dentist is
    a form nobody can book with. So the list is answered by the clinic
    module, behind `clinic.appointment.read`, and carries a name and an id
    and nothing else.
    """

    async def test_it_offers_the_dentists_and_the_owner(
        self, treatments, sales, org_a, db: AsyncSession
    ) -> None:
        """In a clinic of three the owner is usually also the dentist."""
        from app.modules.audit.service import AuditRecorder
        from app.modules.identity.service import IdentityService

        identity = IdentityService(db, AuditRecorder(db, actor_label="test"))
        created = await identity.create_user_in_organization(
            organization_id=sales.id,
            email="roster-dentist@example.com",
            full_name="Roster Dentist",
            password="roster-dentist-password-1",
            role_codes=["dentist"],
        )
        dentist_id = getattr(created, "id", None) or created[0].id

        offered = await treatments.bookable_dentists(organization_id=sales.id)
        ids = [user_id for user_id, _name in offered]
        assert dentist_id in ids
        assert org_a.owner.id in ids

    async def test_it_leaves_out_everybody_else(self, treatments, sales, db: AsyncSession) -> None:
        """A cashier is not somebody a patient can be booked in with."""
        from app.modules.audit.service import AuditRecorder
        from app.modules.identity.service import IdentityService

        identity = IdentityService(db, AuditRecorder(db, actor_label="test"))
        created = await identity.create_user_in_organization(
            organization_id=sales.id,
            email="roster-cashier@example.com",
            full_name="Roster Cashier",
            password="roster-cashier-password-1",
            role_codes=["cashier"],
        )
        cashier_id = getattr(created, "id", None) or created[0].id

        offered = await treatments.bookable_dentists(organization_id=sales.id)
        assert cashier_id not in [user_id for user_id, _name in offered]

    async def test_it_carries_a_name_and_nothing_more(
        self, treatments, sales, db: AsyncSession
    ) -> None:
        from app.modules.audit.service import AuditRecorder
        from app.modules.identity.service import IdentityService

        identity = IdentityService(db, AuditRecorder(db, actor_label="test"))
        await identity.create_user_in_organization(
            organization_id=sales.id,
            email="named-dentist@example.com",
            full_name="Named Dentist",
            password="named-dentist-password-1",
            role_codes=["dentist"],
        )
        offered = await treatments.bookable_dentists(organization_id=sales.id)
        names = [name for _id, name in offered]
        assert "Named Dentist" in names
        assert not any("@" in name for name in names), "an e-mail address is not a name"

    async def test_a_dentist_who_has_left_is_not_offered(
        self, treatments, sales, db: AsyncSession
    ) -> None:
        """A booking handed to somebody who cannot sign in is a booking nobody works."""
        from app.modules.audit.service import AuditRecorder
        from app.modules.identity.service import IdentityService

        identity = IdentityService(db, AuditRecorder(db, actor_label="test"))
        created = await identity.create_user_in_organization(
            organization_id=sales.id,
            email="left-dentist@example.com",
            full_name="Departed Dentist",
            password="left-dentist-password-1",
            role_codes=["dentist"],
        )
        gone_id = getattr(created, "id", None) or created[0].id
        membership = await identity.get_membership(user_id=gone_id, organization_id=sales.id)
        assert membership is not None
        await identity.set_membership_active(
            membership=membership, is_active=False, reason="Left the clinic"
        )
        await db.flush()

        offered = await treatments.bookable_dentists(organization_id=sales.id)
        assert gone_id not in [user_id for user_id, _name in offered]

    async def test_another_clinic_s_dentists_are_not_offered(
        self, treatments, sales, org_b
    ) -> None:
        offered = await treatments.bookable_dentists(organization_id=sales.id)
        assert org_b.owner.id not in [user_id for user_id, _name in offered]


class TestTwoReceptionistsAtOnce:
    async def test_two_bookings_for_the_same_hour_cannot_both_win(
        self, engine, committed_org
    ) -> None:
        """The hour is not free twice.

        Both read the day's bookings and both find the hour empty: the other
        transaction's row is not committed yet, and there is no row to lock
        because the conflict is with one that does not exist. The advisory
        lock on the dentist and the day is what serialises them, so the
        second finds the first's booking and refuses.
        """
        import asyncio

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.modules.audit.service import AuditRecorder
        from app.modules.clinic.services.patients import PatientService
        from app.modules.clinic.services.treatments import TreatmentService

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        org_id = committed_org.id
        dentist_id = committed_org.owner.id

        async with factory() as setup:
            recorder = AuditRecorder(setup, actor_label="test")
            service = PatientService(setup, recorder)
            first, _ = await service.register(
                organization_id=org_id, family_name="Raceov", given_name="Bir"
            )
            second, _ = await service.register(
                organization_id=org_id,
                family_name="Raceov",
                given_name="Ikki",
                allow_duplicate=True,
            )
            first_id, second_id = first.id, second.id
            await setup.commit()

        async def attempt(patient_id, who: str) -> str:
            async with factory() as session:
                treatments = TreatmentService(session, AuditRecorder(session, actor_label=who))
                try:
                    await treatments.book(
                        organization_id=org_id,
                        patient_id=patient_id,
                        scheduled_on=TUESDAY,
                        scheduled_at=time(15, 0),
                        duration_minutes=30,
                        dentist_user_id=dentist_id,
                    )
                    await session.commit()
                    return "booked"
                except Exception as exc:  # which one fails is the point
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(
            attempt(first_id, "reception-one"), attempt(second_id, "reception-two")
        )
        assert results.count("booked") == 1, f"exactly one may succeed, got {results}"

        async with factory() as check:
            treatments = TreatmentService(check, AuditRecorder(check, actor_label="test"))
            rows = await treatments.list_appointments(organization_id=org_id, on=TUESDAY)
            live = [r for r in rows if r.scheduled_at == time(15, 0)]
            assert len(live) == 1, "the hour was booked once, not twice"

    async def test_two_bookings_at_different_hours_both_win(self, engine, committed_org) -> None:
        """The lock serialises the day; it does not close it."""
        import asyncio

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.modules.audit.service import AuditRecorder
        from app.modules.clinic.services.patients import PatientService
        from app.modules.clinic.services.treatments import TreatmentService

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        org_id = committed_org.id
        dentist_id = committed_org.owner.id

        async with factory() as setup:
            recorder = AuditRecorder(setup, actor_label="test")
            service = PatientService(setup, recorder)
            first, _ = await service.register(
                organization_id=org_id, family_name="Parallelov", given_name="Bir"
            )
            second, _ = await service.register(
                organization_id=org_id,
                family_name="Parallelov",
                given_name="Ikki",
                allow_duplicate=True,
            )
            first_id, second_id = first.id, second.id
            await setup.commit()

        async def attempt(patient_id, at, who: str) -> str:
            async with factory() as session:
                treatments = TreatmentService(session, AuditRecorder(session, actor_label=who))
                try:
                    await treatments.book(
                        organization_id=org_id,
                        patient_id=patient_id,
                        scheduled_on=WEDNESDAY,
                        scheduled_at=at,
                        duration_minutes=30,
                        dentist_user_id=dentist_id,
                    )
                    await session.commit()
                    return "booked"
                except Exception as exc:
                    await session.rollback()
                    return type(exc).__name__

        results = await asyncio.gather(
            attempt(first_id, time(9, 0), "reception-one"),
            attempt(second_id, time(10, 0), "reception-two"),
        )
        assert results == ["booked", "booked"], results
