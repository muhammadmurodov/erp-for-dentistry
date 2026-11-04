"""Courses of treatment that span several visits.

A plan is a clinical commitment, not a quotation and not an invoice: it moves
no money and posts nothing. What it has to get right is its own life cycle —
a plan is proposed, agreed, worked and finished, or it is abandoned — and the
rule that it cannot be declared finished while a tooth on it is still open.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.clinic.enums import PlanStatus, TreatmentItemStatus
from app.modules.clinic.services.treatments import ItemInput

VISIT = date(2026, 10, 6)


async def a_patient(patients, sales, *, family="Plannov", given="Dilnoza"):
    patient, _ = await patients.register(
        organization_id=sales.id,
        family_name=family,
        given_name=given,
        birth_year=1988,
    )
    return patient


async def a_plan(treatments, patients, sales, *, title="Upper left bridge"):
    patient = await a_patient(patients, sales)
    plan = await treatments.create_plan(
        organization_id=sales.id, patient_id=patient.id, title=title
    )
    return patient, plan


async def an_item_on(treatments, sales, patient, plan, *, status, service_id):
    """Record one tooth against the plan, through a visit."""
    encounter = await treatments.open_encounter(
        organization_id=sales.id, patient_id=patient.id, occurred_on=VISIT
    )
    items = await treatments.record_items(
        organization_id=sales.id,
        encounter_id=encounter.id,
        plan_id=plan.id,
        items=[
            ItemInput(
                service_id=service_id,
                tooth_number=26,
                status=status,
                quantity=Decimal("1"),
            )
        ],
    )
    return items[0]


class TestCreatingAPlan:
    async def test_it_starts_as_a_draft_with_a_code(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        assert plan.status is PlanStatus.DRAFT
        assert plan.code.startswith("TP-")
        assert plan.started_on is None
        assert plan.completed_on is None

    async def test_two_plans_get_different_codes(self, treatments, patients, sales) -> None:
        _p1, first = await a_plan(treatments, patients, sales)
        _p2, second = await a_plan(treatments, patients, sales, title="Lower right")
        assert first.code != second.code

    async def test_creating_one_is_audited(
        self, treatments, patients, sales, db: AsyncSession
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == sales.id,
                    AuditLog.action == events.PLAN_CREATED,
                )
            )
        ).scalar_one()
        assert row.target_label == plan.code

    async def test_a_plan_for_an_unknown_patient_is_refused(self, treatments, sales) -> None:
        import uuid

        with pytest.raises(NotFound):
            await treatments.create_plan(
                organization_id=sales.id, patient_id=uuid.uuid4(), title="Nobody's bridge"
            )


class TestReadingPlans:
    async def test_a_plan_is_found_by_id(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        found = await treatments.get_plan(organization_id=sales.id, plan_id=plan.id)
        assert found.id == plan.id

    async def test_an_unknown_plan_is_not_found(self, treatments, sales) -> None:
        import uuid

        with pytest.raises(NotFound):
            await treatments.get_plan(organization_id=sales.id, plan_id=uuid.uuid4())

    async def test_plans_filter_by_patient(self, treatments, patients, sales) -> None:
        mine, plan = await a_plan(treatments, patients, sales, title="Mine")
        other = await a_patient(patients, sales, family="Otherov", given="Bek")
        await treatments.create_plan(organization_id=sales.id, patient_id=other.id, title="Theirs")
        rows = await treatments.list_plans(organization_id=sales.id, patient_id=mine.id)
        assert [r.id for r in rows] == [plan.id]

    async def test_plans_filter_by_status(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.PROPOSED
        )
        assert [
            r.id
            for r in await treatments.list_plans(
                organization_id=sales.id, statuses=[PlanStatus.PROPOSED]
            )
        ] == [plan.id]
        assert (
            await treatments.list_plans(organization_id=sales.id, statuses=[PlanStatus.COMPLETED])
            == []
        )

    async def test_a_plan_s_items_come_back_with_it(
        self, treatments, patients, sales, db: AsyncSession
    ) -> None:
        patient, plan = await a_plan(treatments, patients, sales)
        item = await an_item_on(
            treatments,
            sales,
            patient,
            plan,
            status=TreatmentItemStatus.PLANNED,
            service_id=sales.service.id,
        )
        rows = await treatments.plan_items(organization_id=sales.id, plan_id=plan.id)
        assert [r.id for r in rows] == [item.id]
        assert rows[0].plan_id == plan.id

    async def test_work_recorded_without_a_plan_stays_off_it(
        self, treatments, patients, sales
    ) -> None:
        patient, plan = await a_plan(treatments, patients, sales)
        encounter = await treatments.open_encounter(
            organization_id=sales.id, patient_id=patient.id, occurred_on=VISIT
        )
        await treatments.record_items(
            organization_id=sales.id,
            encounter_id=encounter.id,
            items=[ItemInput(service_id=sales.service.id, tooth_number=11)],
        )
        assert await treatments.plan_items(organization_id=sales.id, plan_id=plan.id) == []


class TestTheLifeCycle:
    async def test_the_ordinary_course_runs_to_completion(
        self, treatments, patients, sales
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        for status in (
            PlanStatus.PROPOSED,
            PlanStatus.ACCEPTED,
            PlanStatus.IN_PROGRESS,
            PlanStatus.COMPLETED,
        ):
            plan = await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
            assert plan.status is status

    async def test_starting_work_records_the_day_it_started(
        self, treatments, patients, sales
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        for status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED, PlanStatus.IN_PROGRESS):
            plan = await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
        assert plan.started_on is not None
        assert plan.completed_on is None

    async def test_finishing_records_the_day_it_finished(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        for status in (
            PlanStatus.PROPOSED,
            PlanStatus.ACCEPTED,
            PlanStatus.IN_PROGRESS,
            PlanStatus.COMPLETED,
        ):
            plan = await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
        assert plan.completed_on is not None

    async def test_a_draft_cannot_jump_to_finished(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        with pytest.raises(Conflict) as excinfo:
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=PlanStatus.COMPLETED
            )
        assert "PROPOSED" in excinfo.value.details["allowed"]

    async def test_a_proposal_may_go_back_to_draft(self, treatments, patients, sales) -> None:
        """The patient asks for a change before agreeing to anything."""
        _patient, plan = await a_plan(treatments, patients, sales)
        await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.PROPOSED
        )
        plan = await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.DRAFT
        )
        assert plan.status is PlanStatus.DRAFT

    async def test_a_finished_plan_is_final(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        for status in (
            PlanStatus.PROPOSED,
            PlanStatus.ACCEPTED,
            PlanStatus.IN_PROGRESS,
            PlanStatus.COMPLETED,
        ):
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
        with pytest.raises(Conflict):
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=PlanStatus.IN_PROGRESS
            )

    async def test_an_abandoned_plan_is_final(self, treatments, patients, sales) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.ABANDONED
        )
        with pytest.raises(Conflict):
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=PlanStatus.PROPOSED
            )

    async def test_a_plan_may_be_abandoned_from_any_live_state(
        self, treatments, patients, sales
    ) -> None:
        for reached in (PlanStatus.DRAFT, PlanStatus.PROPOSED, PlanStatus.ACCEPTED):
            _patient, plan = await a_plan(treatments, patients, sales)
            walk = {
                PlanStatus.DRAFT: (),
                PlanStatus.PROPOSED: (PlanStatus.PROPOSED,),
                PlanStatus.ACCEPTED: (PlanStatus.PROPOSED, PlanStatus.ACCEPTED),
            }[reached]
            for status in walk:
                await treatments.set_plan_status(
                    organization_id=sales.id, plan_id=plan.id, status=status
                )
            plan = await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=PlanStatus.ABANDONED
            )
            assert plan.status is PlanStatus.ABANDONED

    async def test_setting_the_status_it_already_has_changes_nothing(
        self, treatments, patients, sales
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        same = await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.DRAFT
        )
        assert same.status is PlanStatus.DRAFT

    async def test_a_change_is_audited_with_both_states(
        self, treatments, patients, sales, db: AsyncSession
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.PROPOSED
        )
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == sales.id,
                    AuditLog.action == events.PLAN_STATUS_CHANGED,
                )
            )
        ).scalar_one()
        assert row.old_values == {"status": "DRAFT"}
        assert row.new_values == {"status": "PROPOSED"}


class TestFinishingWithWorkOutstanding:
    async def test_a_plan_with_unfinished_work_cannot_be_completed(
        self, treatments, patients, sales
    ) -> None:
        patient, plan = await a_plan(treatments, patients, sales)
        await an_item_on(
            treatments,
            sales,
            patient,
            plan,
            status=TreatmentItemStatus.IN_PROGRESS,
            service_id=sales.service.id,
        )
        for status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED, PlanStatus.IN_PROGRESS):
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
        with pytest.raises(Conflict) as excinfo:
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=PlanStatus.COMPLETED
            )
        assert excinfo.value.details["open_items"] == 1

    async def test_once_the_work_is_done_the_plan_completes(
        self, treatments, patients, sales
    ) -> None:
        patient, plan = await a_plan(treatments, patients, sales)
        item = await an_item_on(
            treatments,
            sales,
            patient,
            plan,
            status=TreatmentItemStatus.IN_PROGRESS,
            service_id=sales.service.id,
        )
        for status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED, PlanStatus.IN_PROGRESS):
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
        await treatments.complete_item(organization_id=sales.id, item_id=item.id)
        plan = await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.COMPLETED
        )
        assert plan.status is PlanStatus.COMPLETED

    async def test_cancelled_work_does_not_hold_a_plan_open(
        self, treatments, patients, sales
    ) -> None:
        patient, plan = await a_plan(treatments, patients, sales)
        item = await an_item_on(
            treatments,
            sales,
            patient,
            plan,
            status=TreatmentItemStatus.PLANNED,
            service_id=sales.service.id,
        )
        for status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED, PlanStatus.IN_PROGRESS):
            await treatments.set_plan_status(
                organization_id=sales.id, plan_id=plan.id, status=status
            )
        await treatments.cancel_item(
            organization_id=sales.id, item_id=item.id, reason="Patient declined"
        )
        plan = await treatments.set_plan_status(
            organization_id=sales.id, plan_id=plan.id, status=PlanStatus.COMPLETED
        )
        assert plan.status is PlanStatus.COMPLETED


class TestTenantIsolation:
    async def test_a_plan_is_invisible_from_another_organization(
        self, treatments, patients, sales, org_b
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        with pytest.raises(NotFound):
            await treatments.get_plan(organization_id=org_b.id, plan_id=plan.id)

    async def test_another_organization_cannot_move_it(
        self, treatments, patients, sales, org_b
    ) -> None:
        _patient, plan = await a_plan(treatments, patients, sales)
        with pytest.raises(NotFound):
            await treatments.set_plan_status(
                organization_id=org_b.id, plan_id=plan.id, status=PlanStatus.PROPOSED
            )
