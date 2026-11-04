"""Purchasing over HTTP: who may reach what, and one journey end to end.

The authorization shape is the point. `purchasing.order.manage` raises an
order; `purchasing.order.approve` approves it; they are a
segregation-of-duties pair and no single shipped role holds both. The
accountant registers bills and pays them and may not raise an order; the
inventory manager raises orders and may not approve or pay.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"


async def owner_headers(login, org_a):
    return await login(f"owner@{org_a.organization.code}.example.com", OWNER_PASSWORD)


async def staff_headers(login, org_a, label):
    return await login(f"{label}@{org_a.organization.code}.example.com", STAFF_PASSWORD)


class TestWhoMayReachIt:
    async def test_the_accountant_reads_and_registers_bills(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        headers = await staff_headers(login, org_a, "accountant")
        assert (
            await client.get("/api/v1/purchasing/suppliers", headers=headers)
        ).status_code == 200
        created = await client.post(
            "/api/v1/purchasing/suppliers",
            headers=headers,
            json={"name": "Accountant's supplier"},
        )
        assert created.status_code == 201, created.text

    async def test_the_accountant_may_not_raise_an_order(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        """Raising an order is the inventory manager's job; the accountant's
        role lists purchasing permissions one by one and leaves that out."""
        headers = await staff_headers(login, org_a, "accountant")
        refused = await client.post(
            "/api/v1/purchasing/orders",
            headers=headers,
            json={
                "supplier_id": "00000000-0000-0000-0000-000000000000",
                "lines": [{"description": "x", "quantity": "1", "unit_price": "1000"}],
            },
        )
        assert refused.status_code == 403

    async def test_the_cashier_reaches_nothing(self, client: AsyncClient, login, org_a) -> None:
        headers = await staff_headers(login, org_a, "cashier")
        assert (
            await client.get("/api/v1/purchasing/suppliers", headers=headers)
        ).status_code == 403
        assert (await client.get("/api/v1/purchasing/bills", headers=headers)).status_code == 403

    async def test_the_receptionist_reaches_nothing(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "receptionist")
        assert (await client.get("/api/v1/purchasing/orders", headers=headers)).status_code == 403

    async def test_the_auditor_reads_and_cannot_write(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        headers = await staff_headers(login, org_a, "auditor")
        assert (
            await client.get("/api/v1/purchasing/suppliers", headers=headers)
        ).status_code == 200
        assert (
            await client.get("/api/v1/purchasing/payables/ageing", headers=headers)
        ).status_code == 200
        refused = await client.post(
            "/api/v1/purchasing/suppliers", headers=headers, json={"name": "No"}
        )
        assert refused.status_code == 403

    async def test_an_unauthenticated_caller_gets_nowhere(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/purchasing/suppliers")).status_code == 401


class TestOrderToPayment:
    async def test_the_whole_journey(self, client: AsyncClient, login, org_a, sales) -> None:
        """Order, receive, bill, register, pay — and the bill settles."""
        headers = await owner_headers(login, org_a)

        supplier = await client.post(
            "/api/v1/purchasing/suppliers",
            headers=headers,
            json={"name": "Dental Depot", "tax_number": "302233445"},
        )
        assert supplier.status_code == 201, supplier.text
        supplier_id = supplier.json()["id"]

        order = await client.post(
            "/api/v1/purchasing/orders",
            headers=headers,
            json={
                "supplier_id": supplier_id,
                "ordered_on": "2026-10-01",
                "lines": [
                    {
                        "description": "Composite, A2",
                        "quantity": "10",
                        "unit_price": "50000",
                        "unit": "box",
                    }
                ],
            },
        )
        assert order.status_code == 201, order.text
        assert order.json()["total"] == "500000.00"
        assert order.json()["status"] == "DRAFT"
        order_id = order.json()["id"]

        # The owner may approve an order they raised; it is recorded as such.
        approved = await client.post(
            f"/api/v1/purchasing/orders/{order_id}/status",
            headers=headers,
            json={"status": "APPROVED"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["self_approved"] is True

        detail = await client.get(f"/api/v1/purchasing/orders/{order_id}", headers=headers)
        order_line_id = detail.json()["lines"][0]["id"]
        assert detail.json()["outstanding"][0]["outstanding"] == "10.00"

        receipt = await client.post(
            "/api/v1/purchasing/receipts",
            headers=headers,
            json={
                "supplier_id": supplier_id,
                "order_id": order_id,
                "received_on": "2026-10-03",
                "lines": [
                    {
                        "description": "Composite, A2",
                        "quantity": "10",
                        "order_line_id": order_line_id,
                    }
                ],
            },
        )
        assert receipt.status_code == 201, receipt.text

        after = await client.get(f"/api/v1/purchasing/orders/{order_id}", headers=headers)
        assert after.json()["status"] == "RECEIVED"
        assert after.json()["outstanding"][0]["outstanding"] == "0.00"

        uninvoiced = await client.get(
            f"/api/v1/purchasing/suppliers/{supplier_id}/uninvoiced", headers=headers
        )
        assert len(uninvoiced.json()) == 1
        receipt_line_id = uninvoiced.json()[0]["id"]

        bill = await client.post(
            "/api/v1/purchasing/bills",
            headers=headers,
            json={
                "supplier_id": supplier_id,
                "kind": "MATERIALS",
                "invoice_date": "2026-10-04",
                "due_date": "2026-10-20",
                "supplier_reference": "AKT-4471",
                "lines": [
                    {
                        "description": "Composite, A2",
                        "quantity": "10",
                        "unit_price": "50000",
                        "receipt_line_id": receipt_line_id,
                    }
                ],
            },
        )
        assert bill.status_code == 201, bill.text
        assert bill.json()["status"] == "DRAFT"
        bill_id = bill.json()["id"]

        # Billed once: the delivery line is no longer uninvoiced.
        assert (
            len(
                (
                    await client.get(
                        f"/api/v1/purchasing/suppliers/{supplier_id}/uninvoiced",
                        headers=headers,
                    )
                ).json()
            )
            == 0
        )

        registered = await client.post(
            f"/api/v1/purchasing/bills/{bill_id}/register", headers=headers
        )
        assert registered.status_code == 200, registered.text
        assert registered.json()["status"] == "REGISTERED"
        assert registered.json()["remaining"] == "500000.00"

        ageing = await client.get(
            "/api/v1/purchasing/payables/ageing",
            headers=headers,
            params={"as_of": "2026-10-25"},
        )
        assert ageing.status_code == 200
        assert ageing.json()[0]["days_1_30"] == "500000.00"

        payment = await client.post(
            "/api/v1/purchasing/payments",
            headers=headers,
            json={
                "supplier_id": supplier_id,
                "amount": "500000",
                "method": "BANK",
                "paid_on": "2026-10-20",
                "invoice_id": bill_id,
            },
        )
        assert payment.status_code == 201, payment.text

        settled = await client.get(f"/api/v1/purchasing/bills/{bill_id}", headers=headers)
        assert settled.json()["remaining"] == "0.00"
        assert settled.json()["paid"] == "500000.00"

        balance = await client.get(
            f"/api/v1/purchasing/suppliers/{supplier_id}/balance", headers=headers
        )
        assert balance.json()["outstanding"] == "0.00"


class TestWhatTheApiRefuses:
    async def test_a_float_amount_is_refused(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        headers = await owner_headers(login, org_a)
        supplier = await client.post(
            "/api/v1/purchasing/suppliers", headers=headers, json={"name": "Floatov"}
        )
        refused = await client.post(
            "/api/v1/purchasing/payments",
            headers=headers,
            json={
                "supplier_id": supplier.json()["id"],
                "amount": 500000.1,
                "method": "BANK",
            },
        )
        assert refused.status_code == 422

    async def test_a_cash_payment_needs_an_open_till(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        """Money leaving the till has to move the drawer, or the evening
        count is short by exactly the amount nobody can explain."""
        headers = await owner_headers(login, org_a)
        supplier = await client.post(
            "/api/v1/purchasing/suppliers", headers=headers, json={"name": "Naqdov"}
        )
        refused = await client.post(
            "/api/v1/purchasing/payments",
            headers=headers,
            json={
                "supplier_id": supplier.json()["id"],
                "amount": "100000",
                "method": "CASH",
            },
        )
        assert refused.status_code == 422
        assert refused.json()["details"]["field"] == "cash_session_id"

    async def test_a_till_belongs_only_to_a_cash_payment(
        self, client: AsyncClient, login, org_a, sales
    ) -> None:
        headers = await owner_headers(login, org_a)
        supplier = await client.post(
            "/api/v1/purchasing/suppliers", headers=headers, json={"name": "Bankov"}
        )
        refused = await client.post(
            "/api/v1/purchasing/payments",
            headers=headers,
            json={
                "supplier_id": supplier.json()["id"],
                "amount": "100000",
                "method": "BANK",
                "cash_session_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert refused.status_code == 422

    async def test_another_tenants_supplier_is_not_found(
        self, client: AsyncClient, login, org_a, org_b, sales
    ) -> None:
        mine = await owner_headers(login, org_a)
        theirs = await login(f"owner@{org_b.organization.code}.example.com", OWNER_PASSWORD)
        supplier = await client.post(
            "/api/v1/purchasing/suppliers", headers=mine, json={"name": "Mine"}
        )
        probed = await client.get(
            f"/api/v1/purchasing/suppliers/{supplier.json()['id']}/balance",
            headers=theirs,
        )
        assert probed.status_code == 404
