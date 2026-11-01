"""The dashboard's read model: every figure, and who may see it.

Two kinds of assertion here, and the second kind matters more.

The first is arithmetic: today's takings split by method, work begun and not
finished, completed work nobody has billed. Those are countable, and a test
that counts them is worth little on its own.

The second is agreement. `debt_outstanding` must equal what
`ReceivablesService` says, to the som, because the moment a dashboard and a
report disagree about what the clinic is owed, both become useless — and the
cheapest way to get two answers is to write the derivation twice. The same
goes for the advance available and the drawer's expected cash. Those tests
are the reason this read model borrows its figures instead of computing them.

And the filtering: a section the caller may not read must come back absent,
not zero, and must not be queried at all.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clinic.enums import TreatmentItemStatus
from app.modules.clinic.services.today import TodayService
from app.modules.clinic.services.treatments import ItemInput, TreatmentService
from app.modules.sales.enums import PaymentMethod
from app.modules.sales.services.receivables import ReceivablesService

ISSUE_DATE = date(2026, 10, 1)
TODAY = date.today()


async def _everything(db, audit, organization_id, on=None):
    """The view as the owner sees it: every section included."""
    return await TodayService(db, audit).view(
        organization_id=organization_id,
        business_date=on or TODAY,
        include_appointments=True,
        include_patients=True,
        include_clinical=True,
        include_money=True,
        include_drawer=True,
        include_duplicates=True,
    )


async def _patient(patients, org_id, family="Oripov", given="Jahongir", **kw):
    patient, _ = await patients.register(
        organization_id=org_id,
        family_name=family,
        given_name=given,
        birth_year=kw.pop("birth_year", 1990),
        registered_on=kw.pop("registered_on", TODAY),
        **kw,
    )
    return patient


class TestAnEmptyDay:
    async def test_it_reports_zeroes_rather_than_nothing(
        self, audit, db: AsyncSession, sales, org_a
    ) -> None:
        """A quiet morning is a real answer. Absent means "not your
        business"; zero means "nothing happened"."""
        view = await _everything(db, audit, sales.id)
        assert view.business_date == TODAY
        assert view.appointments is not None and view.appointments.total == 0
        assert view.treatments_in_progress == 0
        assert view.treatments_billable == 0
        assert view.payments is not None and view.payments.total == Decimal("0.00")
        assert view.debt_outstanding == Decimal("0")
        assert view.drawer is None
        assert view.drawer_readable is True


class TestPatientsRegistered:
    async def test_it_counts_today_and_not_yesterday(
        self, audit, db: AsyncSession, sales, patients, org_a
    ) -> None:
        await _patient(patients, sales.id, given="Today")
        await _patient(
            patients, sales.id, family="Karimova", given="Yesterday",
            registered_on=TODAY - timedelta(days=1),
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        assert view.patients_registered == 1

    async def test_a_merged_record_is_not_a_new_patient(
        self, audit, db: AsyncSession, sales, patients, org_a
    ) -> None:
        """Two rows became one person. The clinic saw one patient."""
        first = await _patient(patients, sales.id)
        second = await _patient(patients, sales.id)
        await db.flush()
        assert (await _everything(db, audit, sales.id)).patients_registered == 2

        await patients.merge(
            organization_id=sales.id,
            patient_id=second.id,
            into_patient_id=first.id,
            reason="same person, registered twice",
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).patients_registered == 1


class TestTreatmentCounts:
    async def test_work_begun_and_not_finished_is_counted(
        self, db: AsyncSession, sales, patients, audit, org_a
    ) -> None:
        treatments = TreatmentService(db, audit)
        patient = await _patient(patients, sales.id)
        encounter = await treatments.open_encounter(
            organization_id=sales.id, patient_id=patient.id, occurred_on=TODAY
        )
        await treatments.record_items(
            organization_id=sales.id,
            encounter_id=encounter.id,
            items=[
                ItemInput(service_id=sales.service.id, tooth_number=24),
                ItemInput(
                    service_id=sales.service.id,
                    tooth_number=25,
                    status=TreatmentItemStatus.IN_PROGRESS,
                ),
            ],
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        assert view.treatments_in_progress == 2
        assert view.treatments_billable == 0, "nothing finished, so nothing to bill"

    async def test_completing_moves_an_item_from_open_to_billable(
        self, db: AsyncSession, sales, patients, audit, org_a
    ) -> None:
        treatments = TreatmentService(db, audit)
        patient = await _patient(patients, sales.id)
        encounter = await treatments.open_encounter(
            organization_id=sales.id, patient_id=patient.id, occurred_on=TODAY
        )
        items = await treatments.record_items(
            organization_id=sales.id,
            encounter_id=encounter.id,
            items=[ItemInput(service_id=sales.service.id, tooth_number=24)],
        )
        await treatments.complete_item(
            organization_id=sales.id, item_id=items[0].id, completed_on=TODAY
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        assert view.treatments_in_progress == 0
        assert view.treatments_billable == 1, "finished and unbilled is money not asked for"

    async def test_a_cancelled_item_is_neither_open_nor_billable(
        self, db: AsyncSession, sales, patients, audit, org_a
    ) -> None:
        treatments = TreatmentService(db, audit)
        patient = await _patient(patients, sales.id)
        encounter = await treatments.open_encounter(
            organization_id=sales.id, patient_id=patient.id, occurred_on=TODAY
        )
        items = await treatments.record_items(
            organization_id=sales.id,
            encounter_id=encounter.id,
            items=[ItemInput(service_id=sales.service.id, tooth_number=24)],
        )
        await treatments.cancel_item(
            organization_id=sales.id, item_id=items[0].id, reason="patient declined"
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        assert view.treatments_in_progress == 0
        assert view.treatments_billable == 0

    async def test_billing_an_item_clears_it_from_the_billable_count(
        self, db: AsyncSession, sales, patients, audit, billing, org_a
    ) -> None:
        """The count is "work done and not charged for", so drafting the
        invoice is what makes it go away."""
        treatments = TreatmentService(db, audit)
        patient = await _patient(patients, sales.id)
        encounter = await treatments.open_encounter(
            organization_id=sales.id, patient_id=patient.id, occurred_on=TODAY
        )
        items = await treatments.record_items(
            organization_id=sales.id,
            encounter_id=encounter.id,
            items=[ItemInput(service_id=sales.service.id, tooth_number=24)],
        )
        await treatments.complete_item(
            organization_id=sales.id, item_id=items[0].id, completed_on=TODAY
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).treatments_billable == 1

        await treatments.bill_completed_items(
            organization_id=sales.id, patient_id=patient.id, issue_date=TODAY
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).treatments_billable == 0


class TestTodaysTakings:
    async def test_split_by_method_and_totalled(
        self, audit, db: AsyncSession, sales, billing, org_a
    ) -> None:
        for amount, method in (
            ("400000", PaymentMethod.CASH),
            ("600000", PaymentMethod.CARD),
            ("250000", PaymentMethod.BANK_TRANSFER),
        ):
            await billing.record_payment(
                organization_id=sales.id,
                customer_id=sales.customer.id,
                amount=Decimal(amount),
                method=method,
                received_on=TODAY,
            )
        await db.flush()
        payments = (await _everything(db, audit, sales.id)).payments
        assert payments is not None
        assert payments.cash == Decimal("400000.00")
        assert payments.card == Decimal("600000.00")
        assert payments.transfer == Decimal("250000.00")
        assert payments.total == Decimal("1250000.00")
        assert payments.count == 3

    async def test_a_payment_taken_yesterday_is_not_todays_takings(
        self, audit, db: AsyncSession, sales, billing, org_a
    ) -> None:
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CASH,
            received_on=TODAY - timedelta(days=1),
        )
        await db.flush()
        payments = (await _everything(db, audit, sales.id)).payments
        assert payments is not None and payments.total == Decimal("0.00")

    async def test_a_refunded_payment_is_not_counted(
        self, audit, db: AsyncSession, sales, billing, org_a
    ) -> None:
        """Money that came in and went back out again is not takings."""
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("300000"),
            method=PaymentMethod.CARD,
            received_on=TODAY,
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).payments.total == Decimal("300000.00")

        await billing.refund_payment(
            organization_id=sales.id,
            payment_id=payment.id,
            reason="treatment abandoned",
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).payments.total == Decimal("0.00")


class TestItAgreesWithTheReportsItSummarises:
    """The assertions this file exists for."""

    async def test_the_debt_equals_the_ageing_report_exactly(
        self, audit, db: AsyncSession, sales, billing, bill, org_a
    ) -> None:
        invoice = await bill("900000")
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("250000"),
            method=PaymentMethod.CASH,
            received_on=ISSUE_DATE,
            allocate_to=[(invoice.id, Decimal("250000"))],
        )
        await db.flush()

        view = await _everything(db, audit, sales.id)
        report = await ReceivablesService(db).ageing(organization_id=sales.id, as_of=TODAY)
        assert view.debt_outstanding == report.total_outstanding, (
            "the dashboard and the ageing report must not disagree about what the "
            "clinic is owed"
        )
        assert view.debt_outstanding == Decimal("650000.00")

    async def test_the_advance_equals_what_the_patient_account_shows(
        self, audit, db: AsyncSession, sales, billing, org_a
    ) -> None:
        """Money left with nothing to apply it to, counted the same way the
        patient's own account counts it."""
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("700000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        spare = sum(
            amount
            for _, amount in await billing.unapplied_payments(
                organization_id=sales.id, customer_id=sales.customer.id
            )
        )
        assert view.advances_available == spare == Decimal("700000.00")

    async def test_applying_an_advance_moves_it_out_of_available(
        self, audit, db: AsyncSession, sales, billing, bill, org_a
    ) -> None:
        invoice = await bill("300000")
        payment = await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("500000"),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).advances_available == Decimal("500000.00")

        await billing.allocate(
            organization_id=sales.id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=Decimal("300000"),
        )
        await db.flush()
        assert (await _everything(db, audit, sales.id)).advances_available == Decimal("200000.00")

    async def test_the_drawer_expectation_equals_the_session_totals(
        self, audit, db: AsyncSession, sales, cash, till, posting, org_a
    ) -> None:
        session = await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=TODAY,
            opening_float=Decimal("0"),
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        totals = await cash.totals(organization_id=sales.id, session_id=session.id)
        assert view.drawer is not None
        assert view.drawer.expected == totals.expected
        assert view.drawer.register_code == till.code
        assert view.drawer.from_an_earlier_day is False

    async def test_a_drawer_left_open_from_an_earlier_day_is_flagged(
        self, audit, db: AsyncSession, sales, cash, till, posting, org_a
    ) -> None:
        """Somebody forgot to close the till, and tomorrow's takings are
        about to land in yesterday's session."""
        await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=TODAY - timedelta(days=2),
            opening_float=Decimal("0"),
        )
        await db.flush()
        view = await _everything(db, audit, sales.id)
        assert view.drawer is not None and view.drawer.from_an_earlier_day is True


class TestItTellsTheCallerOnlyWhatTheyMayRead:
    async def test_a_section_not_asked_for_is_absent_not_zero(
        self, audit, db: AsyncSession, sales, billing, bill, org_a
    ) -> None:
        """A receptionist shown `0 UZS` owed would reasonably conclude the
        clinic is owed nothing."""
        await bill("900000")
        await db.flush()
        view = await TodayService(db, audit).view(
            organization_id=sales.id,
            business_date=TODAY,
            include_patients=True,
            include_money=False,
        )
        assert view.debt_outstanding is None
        assert view.advances_available is None
        assert view.payments is None
        assert view.patients_registered is not None

    async def test_every_section_can_be_withheld(
        self, audit, db: AsyncSession, sales, org_a
    ) -> None:
        view = await TodayService(db, audit).view(organization_id=sales.id, business_date=TODAY)
        assert view.appointments is None
        assert view.patients_registered is None
        assert view.treatments_in_progress is None
        assert view.treatments_billable is None
        assert view.payments is None
        assert view.debt_outstanding is None
        assert view.advances_available is None
        assert view.drawer is None
        assert view.drawer_readable is False
        assert view.duplicate_reviews_pending is None
        # Still a usable answer: the date is not a permission.
        assert view.business_date == TODAY

    async def test_a_withheld_drawer_is_distinguishable_from_no_drawer(
        self, audit, db: AsyncSession, sales, cash, till, posting, org_a
    ) -> None:
        """`drawer: null` means two different things, and the screen has to
        tell them apart: no session is open, or it is none of your business."""
        await cash.open_session(
            organization_id=sales.id,
            register_id=till.id,
            business_date=TODAY,
            opening_float=Decimal("0"),
        )
        await db.flush()
        withheld = await TodayService(db, audit).view(
            organization_id=sales.id, business_date=TODAY, include_drawer=False
        )
        assert withheld.drawer is None and withheld.drawer_readable is False

        allowed = await TodayService(db, audit).view(
            organization_id=sales.id, business_date=TODAY, include_drawer=True
        )
        assert allowed.drawer is not None and allowed.drawer_readable is True


class TestItIsScopedToOneClinic:
    async def test_another_organizations_day_is_not_counted(
        self, audit, db: AsyncSession, sales, org_b, billing, org_a
    ) -> None:
        await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal("100000"),
            method=PaymentMethod.CASH,
            received_on=TODAY,
        )
        await db.flush()
        other = await _everything(db, audit, org_b.id)
        assert other.payments is not None and other.payments.total == Decimal("0.00")
        assert other.patients_registered == 0
