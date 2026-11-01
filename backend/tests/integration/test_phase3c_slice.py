"""Phase 3c end to end over HTTP: a patient, a treatment, and the money.

Driven the way the clinic reaches it — register, treat, bill, issue, settle —
through the routes rather than the services, because a service that works
behind a route that does not is not a working system. The first version of
this file caught exactly that: the settlement routes built their response with
`vars()`, which raises on a slots dataclass, so both of them returned a 500
while every service-level test passed.

The scenario is the specification's mixed payment: a 1 000 000 treatment
settled with 400 000 cash and 600 000 card in one submission.
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


class TestClinicSlice:
    async def test_patient_to_ledger(self, client: AsyncClient, login, org_a, sales, till) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        # Read the fixture ids before the first request. The HTTP calls share
        # this test's session and commit through it, and an ORM object read
        # afterwards would want to refresh itself from a sync context.
        service_id = str(sales.service.id)
        register_id = str(till.id)

        # 1. Register a patient. A billing record is created with them, so
        #    there is something to invoice without anybody typing it twice.
        registered = await client.post(
            f"{CLINIC}/patients",
            headers=headers,
            json={
                "family_name": "Testov",
                "given_name": "Test",
                "birth_year": 1990,
                "phone": "+998901234567",
                "registered_on": TREATMENT_DATE,
            },
        )
        assert registered.status_code == 201, registered.text
        patient = registered.json()["patient"]
        assert patient["customer_id"] is not None
        assert registered.json()["duplicate_candidates"] == []

        # 2. Open a visit and record work on one tooth.
        encounter = await client.post(
            f"{CLINIC}/encounters",
            headers=headers,
            json={
                "patient_id": patient["id"],
                "occurred_on": TREATMENT_DATE,
                "complaint": "Pain on the upper right",
            },
        )
        assert encounter.status_code == 201, encounter.text

        items = await client.post(
            f"{CLINIC}/encounters/{encounter.json()['id']}/items",
            headers=headers,
            json={
                "items": [
                    {
                        "service_id": service_id,
                        "tooth_number": 16,
                        "finding": "Deep caries",
                        "status": "IN_PROGRESS",
                    }
                ]
            },
        )
        assert items.status_code == 201, items.text
        item_id = items.json()[0]["id"]
        assert items.json()[0]["unit_price"] is None, "clinical work carries no price"

        # 3. Nothing is billable until the work is finished.
        before = await client.get(f"{CLINIC}/patients/{patient['id']}/billable", headers=headers)
        assert before.json() == []

        completed = await client.post(
            f"{CLINIC}/treatments/{item_id}/complete",
            headers=headers,
            json={"completed_on": TREATMENT_DATE},
        )
        assert completed.status_code == 200, completed.text

        ledger = await balances(client, headers)
        assert ledger == {} or all(value == Decimal("0") for value in ledger.values()), (
            "completing a treatment is not an accounting event"
        )

        # 4. Draft an invoice from the completed work, then issue it. Two
        #    acts, because issuing is what recognises revenue.
        drafted = await client.post(
            f"{CLINIC}/patients/{patient['id']}/invoices",
            headers=headers,
            json={"issue_date": TREATMENT_DATE},
        )
        assert drafted.status_code == 201, drafted.text
        invoice_id = drafted.json()["id"]
        assert drafted.json()["status"] == "DRAFT"
        total = Decimal(drafted.json()["total"])
        assert total > Decimal("0")

        issued = await client.post(
            f"{SALES}/invoices/{invoice_id}/issue",
            headers=headers,
            json={"tax_treatment": "NONE"},
        )
        assert issued.status_code == 200, issued.text

        # 5. The remaining balance, before anything is paid.
        view = await client.get(f"{CLINIC}/invoices/{invoice_id}/settlement", headers=headers)
        assert view.status_code == 200, view.text
        state = view.json()
        assert Decimal(state["remaining"]) == total
        assert state["status"] == "UNPAID"
        assert state["accepts_payment"] is True
        assert state["open_cash_session_id"] is None, "no drawer is open yet"

        # 6. Cash needs an open drawer, or the day's count reports a shortage
        #    that is not one.
        refused = await client.post(
            f"{CLINIC}/invoices/{invoice_id}/settlement",
            headers=headers,
            json={"cash": str(total), "request_key": "slice-cash-without-a-drawer"},
        )
        assert refused.status_code == 409, refused.text
        assert "cash session" in refused.json()["message"].lower()

        opened = await client.post(
            f"{CASH}/sessions",
            headers=headers,
            json={
                "register_id": register_id,
                "business_date": TREATMENT_DATE,
                "opening_float": "0",
            },
        )
        assert opened.status_code == 201, opened.text

        # 7. One submission, two methods. Checked once against the balance.
        half = total / 2
        settled = await client.post(
            f"{CLINIC}/invoices/{invoice_id}/settlement",
            headers=headers,
            json={
                "cash": str(half),
                "card": str(total - half),
                "received_on": TREATMENT_DATE,
                "request_key": "slice-mixed-payment-0001",
            },
        )
        assert settled.status_code == 200, settled.text
        result = settled.json()
        assert result["replayed"] is False
        assert Decimal(result["view"]["remaining"]) == Decimal("0.00")
        assert result["view"]["status"] == "FULLY_PAID"
        assert result["view"]["accepts_payment"] is False
        assert result["cash_payment_id"] and result["card_payment_id"]

        # 8. The same submission again: replayed, not charged again.
        again = await client.post(
            f"{CLINIC}/invoices/{invoice_id}/settlement",
            headers=headers,
            json={
                "cash": str(half),
                "card": str(total - half),
                "received_on": TREATMENT_DATE,
                "request_key": "slice-mixed-payment-0001",
            },
        )
        assert again.status_code == 200, again.text
        assert again.json()["replayed"] is True
        assert again.json()["cash_payment_id"] == result["cash_payment_id"]

        # 9. And a genuinely new submission is refused, because there is
        #    nothing left to pay.
        extra = await client.post(
            f"{CLINIC}/invoices/{invoice_id}/settlement",
            headers=headers,
            json={"card": "1000", "request_key": "slice-one-payment-too-many"},
        )
        assert extra.status_code == 409, extra.text
        assert "paid in full" in extra.json()["message"]

        # 10. The books: revenue once, receivable cleared, money where it
        #     physically is.
        ledger = await balances(client, headers)
        assert ledger["9030"] == -total, "revenue counted once, not twice"
        assert ledger["4010"] == Decimal("0.00"), "no receivable left"
        assert ledger["5010"] == half, "the cash half is in the drawer"
        assert ledger["5110.03"] == total - half, "the card half is in transit"

        # 11. The drawer agrees with the ledger, which is the whole point of
        #     refusing cash outside a session.
        session_detail = await client.get(f"{CASH}/sessions/{opened.json()['id']}", headers=headers)
        assert Decimal(session_detail.json()["totals"]["expected"]) == half

        # 12. The patient page, as a cashier sees it.
        detail = await client.get(f"{CLINIC}/patients/{patient['id']}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert Decimal(detail.json()["balance"]["outstanding"]) == Decimal("0.00")
        assert detail.json()["billable_items"] == 0, "the work has been billed"
        row = detail.json()["invoices"][0]
        assert row["settlement_status"] == "FULLY_PAID"
        assert Decimal(row["remaining"]) == Decimal("0.00")

        # 13. And the history shows both halves, by method.
        history = await client.get(
            f"{CLINIC}/invoices/{invoice_id}/settlement/history", headers=headers
        )
        assert history.status_code == 200, history.text
        methods = {row["method"]: Decimal(row["amount"]) for row in history.json()}
        assert methods == {"CASH": half, "CARD": total - half}

    async def test_a_cashier_cannot_read_the_clinical_record(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        """The access split is the design, so it is asserted over HTTP.

        A cashier takes money and sees a patient's name and debt. A complaint,
        a finding and a tooth are none of their business, and the route that
        would return them refuses before it reads anything.
        """
        from tests.conftest import STAFF_PASSWORD

        cashier = org_a.members.get("cashier")
        if cashier is None:
            pytest.skip("the org_a fixture defines no cashier")
        headers = await login(cashier[0].email, STAFF_PASSWORD)

        registered = await client.post(
            f"{CLINIC}/patients",
            headers=headers,
            json={"family_name": "Testov", "given_name": "Test"},
        )
        # Registering is reception's job, not the cashier's.
        assert registered.status_code == 403, registered.text

        listing = await client.get(f"{CLINIC}/patients", headers=headers)
        assert listing.status_code == 200, "a cashier may find a patient"

        clinical = await client.get(f"{CLINIC}/patients/{org_a.id}/encounters", headers=headers)
        assert clinical.status_code == 403, "but not read their clinical record"

    async def test_a_cash_refund_goes_through_the_register(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        """ADR-040, over HTTP: the ledger and the drawer move together.

        The generic sales refund route refuses a cash payment for the same
        reason it refuses to take one — reversing the entry without recording
        that the money left a till leaves the drawer expecting it, and the
        day's count turns real cash into a variance loss.
        """
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        service_id = str(sales.service.id)
        register_id = str(till.id)

        patient = (
            await client.post(
                f"{CLINIC}/patients",
                headers=headers,
                json={"family_name": "Refundov", "given_name": "Refund"},
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
                json={"items": [{"service_id": service_id, "tooth_number": 24}]},
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
        await client.post(
            f"{SALES}/invoices/{invoice_id}/issue",
            headers=headers,
            json={"tax_treatment": "NONE"},
        )
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

        view = (
            await client.get(f"{CLINIC}/invoices/{invoice_id}/settlement", headers=headers)
        ).json()
        total = Decimal(view["remaining"])
        settled = await client.post(
            f"{CLINIC}/invoices/{invoice_id}/settlement",
            headers=headers,
            json={
                "cash": str(total),
                "received_on": TREATMENT_DATE,
                "request_key": "slice-refund-cash-0001",
            },
        )
        assert settled.status_code == 200, settled.text
        cash_payment_id = settled.json()["cash_payment_id"]

        # The generic route refuses it and says where to go.
        refused = await client.post(
            f"{SALES}/payments/{cash_payment_id}/refund",
            headers=headers,
            json={"reason": "Treatment abandoned by the patient"},
        )
        assert refused.status_code == 422, refused.text
        assert "cash session" in refused.json()["message"].lower()

        refunded = await client.post(
            f"{CASH}/sessions/{session_id}/refunds",
            headers=headers,
            json={
                "payment_id": cash_payment_id,
                "reason": "Treatment abandoned by the patient",
            },
        )
        assert refunded.status_code == 200, refunded.text
        assert refunded.json()["status"] == "REFUNDED"

        detail = await client.get(f"{CASH}/sessions/{session_id}", headers=headers)
        totals = detail.json()["totals"]
        ledger = await balances(client, headers)
        assert Decimal(totals["refunds"]) == total
        assert Decimal(totals["expected"]) == Decimal("0.00"), "the till is empty again"
        assert ledger.get("5010", Decimal("0")) == Decimal("0.00")
        assert ledger["4010"] == total, "the receivable is back"

        # The bill is payable again: a refund creates a new balance.
        after = (
            await client.get(f"{CLINIC}/invoices/{invoice_id}/settlement", headers=headers)
        ).json()
        assert Decimal(after["remaining"]) == total
        assert after["accepts_payment"] is True

    async def test_a_card_refund_still_goes_through_sales(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        """The refusal is about cash, not about refunds."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        payment = await client.post(
            f"{SALES}/payments",
            headers=headers,
            json={
                "customer_id": str(sales.customer.id),
                "amount": "250000",
                "method": "CARD",
                "received_on": TREATMENT_DATE,
            },
        )
        assert payment.status_code == 201, payment.text
        refunded = await client.post(
            f"{SALES}/payments/{payment.json()['id']}/refund",
            headers=headers,
            json={"reason": "Terminal charged the wrong card"},
        )
        assert refunded.status_code == 200, refunded.text
        assert refunded.json()["status"] == "REFUNDED"
