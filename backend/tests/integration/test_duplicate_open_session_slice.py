"""Opening the till twice, over HTTP.

The service-level test for this cannot see the defect: inside one test
transaction the register is still loaded, so building the message needs no
query. Over HTTP it does, and the 409 the code raises arrived as a 500.

Driven through the route for that reason, and kept as an integration test
rather than moved into the service suite — the bug lived in the difference
between them.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

OWNER_PASSWORD = "fixture-owner-password-1"
CASH = "/api/v1/cash"
BUSINESS_DATE = "2026-10-01"


class TestOpeningTheTillTwice:
    async def test_the_second_attempt_is_a_conflict_that_names_the_register(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        register_id = str(till.id)
        body = {
            "register_id": register_id,
            "business_date": BUSINESS_DATE,
            "opening_float": "0",
        }

        first = await client.post(f"{CASH}/sessions", headers=headers, json=body)
        assert first.status_code == 201, first.text

        second = await client.post(f"{CASH}/sessions", headers=headers, json=body)
        assert second.status_code == 409, second.text
        payload = second.json()
        assert payload["code"] == "conflict"
        assert "already has an open session" in payload["message"]
        # The cashier has to know which till to go and close.
        assert payload["details"]["register_code"] == "TILL-1"


class TestApplyingTheSameAdvanceTwice:
    """The same latent defect, in the double-allocation guard.

    Its message names the payment and the invoice, which it could not read
    once the flush had failed — so a double-clicked allocation returned a 500
    instead of saying the money was already applied.
    """

    async def test_the_second_allocation_is_a_conflict_that_explains_itself(
        self, client: AsyncClient, login, org_a, sales, till
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        customer_id, service_id = str(sales.customer.id), str(sales.service.id)

        invoice_id = (
            await client.post(
                "/api/v1/sales/invoices",
                headers=headers,
                json={
                    "customer_id": customer_id,
                    "issue_date": BUSINESS_DATE,
                    "lines": [
                        {
                            "description": "Canal treatment",
                            "service_id": service_id,
                            "unit_price": "300000",
                        }
                    ],
                },
            )
        ).json()["id"]
        issued = await client.post(
            f"/api/v1/sales/invoices/{invoice_id}/issue",
            headers=headers,
            json={"tax_treatment": "NONE"},
        )
        assert issued.status_code == 200, issued.text

        advance = await client.post(
            "/api/v1/sales/payments",
            headers=headers,
            json={
                "customer_id": customer_id,
                "amount": "300000",
                "method": "CARD",
                "received_on": BUSINESS_DATE,
                "auto_allocate": False,
            },
        )
        assert advance.status_code == 201, advance.text
        payment_id = advance.json()["id"]

        body = [{"invoice_id": invoice_id, "amount": "150000"}]
        first = await client.post(
            f"/api/v1/sales/payments/{payment_id}/allocate", headers=headers, json=body
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"/api/v1/sales/payments/{payment_id}/allocate", headers=headers, json=body
        )
        assert second.status_code == 409, second.text
        assert "already applied to invoice" in second.json()["message"]
