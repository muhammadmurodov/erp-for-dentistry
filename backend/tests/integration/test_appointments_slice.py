"""Appointments over HTTP: the day, the hour, and the clash.

Driven through the routes because the parts that go wrong are at the edge —
whether a time survives the round trip as the clinic's own time, and whether
an overlap arrives as a 409 that names the booking it clashed with or as a
500 with a request id.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

OWNER_PASSWORD = "fixture-owner-password-1"
CLINIC = "/api/v1/clinic"
TUESDAY = "2026-10-06"
WEDNESDAY = "2026-10-07"


async def a_patient(client, headers, family: str) -> dict:
    created = await client.post(
        f"{CLINIC}/patients",
        headers=headers,
        json={"family_name": family, "given_name": "Patient", "birth_year": 1990},
    )
    assert created.status_code == 201, created.text
    return created.json()["patient"]


class TestBookingOverHttp:
    async def test_the_hour_survives_the_round_trip(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        patient = await a_patient(client, headers, "Vaqtov")

        booked = await client.post(
            f"{CLINIC}/appointments",
            headers=headers,
            json={
                "patient_id": patient["id"],
                "scheduled_on": TUESDAY,
                "scheduled_at": "15:00:00",
                "duration_minutes": 30,
                "reason": "Pain in the lower left",
            },
        )
        assert booked.status_code == 201, booked.text
        row = booked.json()
        # Not shifted by a timezone on the way out: 15:00 in the clinic is
        # 15:00 in the response.
        assert row["scheduled_at"] == "15:00:00"
        assert row["scheduled_on"] == TUESDAY
        assert row["status"] == "SCHEDULED"

        listing = await client.get(
            f"{CLINIC}/appointments", headers=headers, params={"on": TUESDAY}
        )
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        assert len(rows) == 1
        assert rows[0]["scheduled_at"] == "15:00:00"
        # The names the screen needs, resolved on the server.
        assert rows[0]["patient_name"] == patient["display_name"]
        assert rows[0]["patient_code"] == patient["code"]
        assert rows[0]["encounter_id"] is None

    async def test_an_overlap_is_a_conflict_that_names_the_booking(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        first = await a_patient(client, headers, "Birinchiev")
        second = await a_patient(client, headers, "Ikkinchiev")
        dentist_id = str(org_a.owner.id)

        await client.post(
            f"{CLINIC}/appointments",
            headers=headers,
            json={
                "patient_id": first["id"],
                "scheduled_on": TUESDAY,
                "scheduled_at": "15:00:00",
                "duration_minutes": 30,
                "dentist_user_id": dentist_id,
            },
        )
        clash = await client.post(
            f"{CLINIC}/appointments",
            headers=headers,
            json={
                "patient_id": second["id"],
                "scheduled_on": TUESDAY,
                "scheduled_at": "15:15:00",
                "duration_minutes": 30,
                "dentist_user_id": dentist_id,
            },
        )
        assert clash.status_code == 409, clash.text
        assert "already booked" in clash.json()["message"]
        assert clash.json()["details"]["scheduled_at"] == "15:00:00"

    async def test_back_to_back_is_allowed(self, client: AsyncClient, login, org_a) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        first = await a_patient(client, headers, "Oldinov")
        second = await a_patient(client, headers, "Keyinov")
        dentist_id = str(org_a.owner.id)

        for patient, at in ((first, "15:00:00"), (second, "15:30:00")):
            booked = await client.post(
                f"{CLINIC}/appointments",
                headers=headers,
                json={
                    "patient_id": patient["id"],
                    "scheduled_on": TUESDAY,
                    "scheduled_at": at,
                    "duration_minutes": 30,
                    "dentist_user_id": dentist_id,
                },
            )
            assert booked.status_code == 201, booked.text

    async def test_a_booking_can_be_moved_and_the_old_hour_frees(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        patient = await a_patient(client, headers, "Kochirov")
        other = await a_patient(client, headers, "Boshqaev")
        dentist_id = str(org_a.owner.id)

        booked = (
            await client.post(
                f"{CLINIC}/appointments",
                headers=headers,
                json={
                    "patient_id": patient["id"],
                    "scheduled_on": TUESDAY,
                    "scheduled_at": "15:00:00",
                    "duration_minutes": 30,
                    "dentist_user_id": dentist_id,
                },
            )
        ).json()

        moved = await client.post(
            f"{CLINIC}/appointments/{booked['id']}/reschedule",
            headers=headers,
            json={
                "scheduled_on": WEDNESDAY,
                "scheduled_at": "11:00:00",
                "duration_minutes": 45,
                "reason": "The patient asked for the morning",
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["scheduled_on"] == WEDNESDAY
        assert moved.json()["scheduled_at"] == "11:00:00"

        # The hour it left is free.
        taken = await client.post(
            f"{CLINIC}/appointments",
            headers=headers,
            json={
                "patient_id": other["id"],
                "scheduled_on": TUESDAY,
                "scheduled_at": "15:00:00",
                "duration_minutes": 30,
                "dentist_user_id": dentist_id,
            },
        )
        assert taken.status_code == 201, taken.text

    async def test_cancelling_needs_a_reason_and_keeps_the_record(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        patient = await a_patient(client, headers, "Bekorov")
        booked = (
            await client.post(
                f"{CLINIC}/appointments",
                headers=headers,
                json={"patient_id": patient["id"], "scheduled_on": TUESDAY},
            )
        ).json()

        refused = await client.post(
            f"{CLINIC}/appointments/{booked['id']}/status",
            headers=headers,
            json={"status": "CANCELLED"},
        )
        assert refused.status_code == 422, refused.text

        cancelled = await client.post(
            f"{CLINIC}/appointments/{booked['id']}/status",
            headers=headers,
            json={"status": "CANCELLED", "reason": "The patient rang to call it off"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"

        # Kept, not deleted: it is still in the day's list.
        rows = (
            await client.get(f"{CLINIC}/appointments", headers=headers, params={"on": TUESDAY})
        ).json()
        assert [r["id"] for r in rows] == [booked["id"]]
        assert rows[0]["cancelled_reason"] == "The patient rang to call it off"

    async def test_the_visit_opened_against_a_booking_is_linked(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        patient = await a_patient(client, headers, "Keldiev")
        booked = (
            await client.post(
                f"{CLINIC}/appointments",
                headers=headers,
                json={"patient_id": patient["id"], "scheduled_on": TUESDAY},
            )
        ).json()

        encounter = await client.post(
            f"{CLINIC}/encounters",
            headers=headers,
            json={
                "patient_id": patient["id"],
                "appointment_id": booked["id"],
                "occurred_on": TUESDAY,
            },
        )
        assert encounter.status_code == 201, encounter.text

        rows = (
            await client.get(f"{CLINIC}/appointments", headers=headers, params={"on": TUESDAY})
        ).json()
        assert rows[0]["status"] == "ARRIVED", "opening a visit means the patient is here"
        assert rows[0]["encounter_id"] == encounter.json()["id"]

    async def test_a_booking_creates_no_invoice(self, client: AsyncClient, login, org_a) -> None:
        """An appointment is a promise about time, not about money."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        patient = await a_patient(client, headers, "Pulsizov")
        before = (await client.get("/api/v1/sales/invoices", headers=headers)).json()
        await client.post(
            f"{CLINIC}/appointments",
            headers=headers,
            json={
                "patient_id": patient["id"],
                "scheduled_on": TUESDAY,
                "scheduled_at": "15:00:00",
                "duration_minutes": 30,
            },
        )
        after = (await client.get("/api/v1/sales/invoices", headers=headers)).json()
        assert len(after) == len(before)

    async def test_the_cashier_reads_the_day_but_may_not_book(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Where the line actually falls.

        The cashier holds `clinic.appointment.read` and not `.manage`: the
        person taking the money is asked all morning who is still expected,
        and answering that is not the same as owning the book. The dentist
        holds `.manage` on purpose — in a clinic of three the dentist
        answers the telephone as often as anybody.
        """
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        patient = await a_patient(client, headers, "Huquqov")
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": "appointment-cashier@example.com",
                "full_name": "Appointment Cashier",
                "password": "appointment-cashier-password-1",
                "role_codes": ["cashier"],
            },
        )
        assert created.status_code == 201, created.text
        cashier = await login("appointment-cashier@example.com", "appointment-cashier-password-1")

        refused = await client.post(
            f"{CLINIC}/appointments",
            headers=cashier,
            json={"patient_id": patient["id"], "scheduled_on": TUESDAY},
        )
        assert refused.status_code == 403, refused.text

        reading = await client.get(
            f"{CLINIC}/appointments", headers=cashier, params={"on": TUESDAY}
        )
        assert reading.status_code == 200, reading.text


class TestWhoMayTakeABookingOverHttp:
    """The roster the appointment form is drawn from.

    Over HTTP because the whole point of it is the permission: reception
    books the day and may not read the staff directory, and that gate lives
    in the router, so only a request exercises it.
    """

    async def _a_receptionist(self, client: AsyncClient, login, org_a) -> dict:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": "roster-reception@example.com",
                "full_name": "Roster Receptionist",
                "password": "roster-reception-password-1",
                "role_codes": ["receptionist"],
            },
        )
        assert created.status_code == 201, created.text
        return await login("roster-reception@example.com", "roster-reception-password-1")

    async def test_reception_reads_the_roster_without_the_directory(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """The defect this endpoint exists for.

        Reception was offered an empty dentist picker, because the form
        asked `/users` and reception does not hold `identity.user.read`.
        Both halves are asserted here: the directory is still refused, and
        the roster is still answered.
        """
        reception = await self._a_receptionist(client, login, org_a)

        directory = await client.get("/api/v1/users", headers=reception)
        assert directory.status_code == 403, directory.text

        roster = await client.get(f"{CLINIC}/dentists", headers=reception)
        assert roster.status_code == 200, roster.text
        rows = roster.json()
        assert rows, "reception cannot book with a picker that offers nobody"
        assert str(org_a.owner.id) in [row["id"] for row in rows]

    async def test_it_answers_a_name_and_an_id_and_nothing_else(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A roster is not a staff directory with a different name on it."""
        reception = await self._a_receptionist(client, login, org_a)
        rows = (await client.get(f"{CLINIC}/dentists", headers=reception)).json()
        assert rows
        for row in rows:
            assert set(row) == {"id", "full_name"}, row

    async def test_somebody_who_may_not_see_the_day_may_not_see_the_roster(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": "roster-accountant@example.com",
                "full_name": "Roster Accountant",
                "password": "roster-accountant-password-1",
                "role_codes": ["accountant"],
            },
        )
        assert created.status_code == 201, created.text
        accountant = await login("roster-accountant@example.com", "roster-accountant-password-1")

        refused = await client.get(f"{CLINIC}/dentists", headers=accountant)
        assert refused.status_code == 403, refused.text
