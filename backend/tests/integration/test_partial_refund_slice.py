"""Giving part of a payment back, over HTTP and out of the drawer.

The clinic's commonest refund: a patient hands over more cash than the bill
and wants the difference. Driven through the routes, because the arithmetic
being right in the service proves nothing about whether the till agrees — and
the till is what somebody counts at the end of the day.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

OWNER_PASSWORD = "fixture-owner-password-1"
CLINIC = "/api/v1/clinic"
SALES = "/api/v1/sales"
CASH = "/api/v1/cash"
ACCOUNTING = "/api/v1/accounting"

TREATMENT_DATE = "2026-10-01"


async def balances(client: AsyncClient, headers: dict[str, str]) -> dict[str, Decimal]:
    report = await client.get(
        f"{ACCOUNTING}/reports/trial-balance",
        headers=headers,
        params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
    )
    assert report.status_code == 200, report.text
    return {
        row["account_code"]: Decimal(row["closing_debit"]) - Decimal(row["closing_credit"])
        for row in report.json()["rows"]
    }


async def an_overpaid_bill(client, headers, *, service_id, register_id, paid="500000"):
    """A 300 000 bill, settled with `paid` in cash in one payment.

    The surplus is unapplied — an advance, money the clinic owes back — which
    is exactly the state a refund comes out of.
    """
    patient = (
        await client.post(
            f"{CLINIC}/patients",
            headers=headers,
            json={"family_name": "Qaytarov", "given_name": "Aziz"},
        )
    ).json()["patient"]
    encounter = await client.post(
        f"{CLINIC}/encounters",
        headers=headers,
        json={"patient_id": patient["id"], "occurred_on": TREATMENT_DATE},
    )
    item = (
        await client.post(
            f"{CLINIC}/encounters/{encounter.json()['id']}/items",
            headers=headers,
            json={"items": [{"service_id": service_id, "tooth_number": 36}]},
        )
    ).json()[0]
    await client.post(
        f"{CLINIC}/treatments/{item['id']}/complete",
        headers=headers,
        json={"completed_on": TREATMENT_DATE},
    )
    invoice_id = (
        await client.post(
            f"{CLINIC}/patients/{patient['id']}/invoices",
            headers=headers,
            json={"issue_date": TREATMENT_DATE},
        )
    ).json()["id"]
    issued = await client.post(
        f"{SALES}/invoices/{invoice_id}/issue",
        headers=headers,
        json={"tax_treatment": "NONE"},
    )
    assert issued.status_code == 200, issued.text
    bill = Decimal(issued.json()["total"])

    session_id = (
        await client.post(
            f"{CASH}/sessions",
            headers=headers,
            json={
                "register_id": register_id,
                "business_date": TREATMENT_DATE,
                "opening_float": "0",
            },
        )
    ).json()["id"]

    # One payment: part of it settles the bill, the rest is spare money.
    taken = await client.post(
        f"{CASH}/sessions/{session_id}/payments",
        headers=headers,
        json={
            "customer_id": patient["customer_id"],
            "amount": paid,
            "received_on": TREATMENT_DATE,
            "allocations": [{"invoice_id": invoice_id, "amount": str(bill)}],
            "auto_allocate": False,
        },
    )
    assert taken.status_code == 201, taken.text
    return {
        "patient": patient,
        "invoice_id": invoice_id,
        "session_id": session_id,
        "payment_id": taken.json()["id"],
        "bill": bill,
        "surplus": Decimal(paid) - bill,
    }


class TestPartialCashRefunds:
    async def test_the_spare_money_goes_back_and_the_till_agrees(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        service_id, register_id = str(sales.service.id), str(till.id)
        state = await an_overpaid_bill(
            client, headers, service_id=service_id, register_id=register_id
        )
        session_id, advance_id = state["session_id"], state["payment_id"]

        before = (await client.get(f"{CASH}/sessions/{session_id}", headers=headers)).json()
        assert Decimal(before["totals"]["expected"]) == Decimal("500000.00")

        # What may be given back, read before anything is offered.
        view = (
            await client.get(f"{SALES}/payments/{advance_id}/refundability", headers=headers)
        ).json()
        assert Decimal(view["amount"]) == Decimal("500000.00")
        assert Decimal(view["allocated"]) == Decimal("300000.00")
        assert Decimal(view["refundable"]) == Decimal("200000.00")
        assert view["may_refund"] is True
        assert view["needs_cash_session"] is True
        assert view["refunds"] == []

        # Half of it back now.
        refunded = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={
                "payment_id": advance_id,
                "amount": "100000",
                "reason": "Patient handed over too much cash",
            },
        )
        assert refunded.status_code == 200, refunded.text
        assert refunded.json()["status"] == "RECORDED", "the payment is still live"

        after = (await client.get(f"{CASH}/sessions/{session_id}", headers=headers)).json()
        assert Decimal(after["totals"]["refunds"]) == Decimal("100000.00")
        assert Decimal(after["totals"]["expected"]) == Decimal("400000.00")

        ledger = await balances(client, headers)
        assert ledger["5010"] == Decimal("400000.00"), "the drawer and 5010 agree"
        assert ledger.get("6310", Decimal("0")) == Decimal("-100000.00"), (
            "half the advance is still owed back"
        )
        assert ledger["9030"] == Decimal("-300000.00"), "revenue is untouched"

        second = (
            await client.get(f"{SALES}/payments/{advance_id}/refundability", headers=headers)
        ).json()
        assert Decimal(second["refunded"]) == Decimal("100000.00")
        assert Decimal(second["refundable"]) == Decimal("100000.00")
        assert len(second["refunds"]) == 1
        assert second["refunds"][0]["reason"] == "Patient handed over too much cash"

    async def test_the_rest_may_follow_and_then_nothing_is_left(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        state = await an_overpaid_bill(
            client, headers, service_id=str(sales.service.id), register_id=str(till.id)
        )
        session_id, advance_id = state["session_id"], state["payment_id"]

        for amount in ("120000", "80000"):
            given = await client.post(
                f"{CASH}/sessions/{session_id}/refunds",
                headers=headers,
                json={
                    "payment_id": advance_id,
                    "amount": amount,
                    "reason": "Returned in two parts",
                },
            )
            assert given.status_code == 200, given.text

        view = (
            await client.get(f"{SALES}/payments/{advance_id}/refundability", headers=headers)
        ).json()
        assert Decimal(view["refundable"]) == Decimal("0.00")
        assert view["may_refund"] is False

        refused = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={"payment_id": advance_id, "amount": "1000", "reason": "One more"},
        )
        assert refused.status_code == 409, refused.text
        assert "nothing left to refund" in refused.json()["message"]

        ledger = await balances(client, headers)
        assert ledger["5010"] == Decimal("300000.00"), "only the bill's money is left"
        assert ledger.get("6310", Decimal("0")) == Decimal("0.00")

    async def test_more_than_is_refundable_is_refused(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        state = await an_overpaid_bill(
            client, headers, service_id=str(sales.service.id), register_id=str(till.id)
        )
        refused = await client.post(
            f"{CASH}/sessions/{state['session_id']}/refunds",
            headers=headers,
            json={
                "payment_id": state["payment_id"],
                "amount": "250000",
                "reason": "More than the patient left",
            },
        )
        assert refused.status_code == 422, refused.text
        assert "200000.00" in refused.json()["message"]

        ledger = await balances(client, headers)
        assert ledger["5010"] == Decimal("500000.00"), "nothing left the till"

    async def test_money_already_on_a_bill_is_not_refundable_this_way(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        """It would refund the patient and leave them owing for the same work.

        Crediting an issued invoice needs an approved credit-note rule, which
        the clinic does not have, so the system refuses and says so rather
        than inventing one.
        """
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        state = await an_overpaid_bill(
            client,
            headers,
            service_id=str(sales.service.id),
            register_id=str(till.id),
            paid="300000",
        )
        assert state["surplus"] == Decimal("0")

        view = (
            await client.get(
                f"{SALES}/payments/{state['payment_id']}/refundability", headers=headers
            )
        ).json()
        assert Decimal(view["refundable"]) == Decimal("0.00")
        assert view["may_refund"] is False

        refused = await client.post(
            f"{CASH}/sessions/{state['session_id']}/refunds",
            headers=headers,
            json={
                "payment_id": state["payment_id"],
                "amount": "50000",
                "reason": "Patient changed their mind about the treatment",
            },
        )
        assert refused.status_code == 409, refused.text
        assert "credited" in refused.json()["message"]

        ledger = await balances(client, headers)
        assert ledger["5010"] == Decimal("300000.00"), "nothing left the till"
        assert ledger["9030"] == Decimal("-300000.00"), "revenue stands"

    async def test_a_refund_needs_a_drawer_that_is_open(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        state = await an_overpaid_bill(
            client, headers, service_id=str(sales.service.id), register_id=str(till.id)
        )
        session_id = state["session_id"]

        counted = await client.post(
            f"{CASH}/sessions/{session_id}/count",
            headers=headers,
            json={"counted_amount": "500000"},
        )
        assert counted.status_code == 200, counted.text
        closed = await client.post(
            f"{CASH}/sessions/{session_id}/close",
            headers=headers,
            json={"duties_cannot_be_separated": True},
        )
        assert closed.status_code == 200, closed.text

        refused = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={
                "payment_id": state["payment_id"],
                "amount": "50000",
                "reason": "After the till was closed",
            },
        )
        assert refused.status_code == 409, refused.text
        assert "cannot refund" in refused.json()["message"]

        ledger = await balances(client, headers)
        assert ledger["5010"] == Decimal("500000.00"), "nothing left the closed till"

    async def test_reception_cannot_refund_anything(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        """Neither approval authority nor the request side of it."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        state = await an_overpaid_bill(
            client, headers, service_id=str(sales.service.id), register_id=str(till.id)
        )
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": "reception-refund@example.com",
                "full_name": "Reception Refund",
                "password": "reception-refund-password-1",
                "role_codes": ["receptionist"],
            },
        )
        assert created.status_code == 201, created.text
        reception = await login("reception-refund@example.com", "reception-refund-password-1")

        refused = await client.post(
            f"{CASH}/sessions/{state['session_id']}/refunds",
            headers=reception,
            json={
                "payment_id": state["payment_id"],
                "amount": "10000",
                "reason": "Reception should not be able to do this",
            },
        )
        assert refused.status_code == 403, refused.text
