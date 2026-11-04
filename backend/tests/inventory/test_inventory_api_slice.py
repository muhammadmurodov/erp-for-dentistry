"""Inventory over HTTP: who may reach it, and what a refusal says.

The service tests cover the rules. These cover the boundary — and one rule
that only exists at this layer: there is **no endpoint that creates a
receipt**. Stock arrives through the goods receipt that justifies it, so a
quantity on a shelf can always be traced back to a delivery note, and no
amount of permission lets somebody type stock into existence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"


async def owner_headers(login, org_a):
    return await login(f"owner@{org_a.organization.code}.example.com", OWNER_PASSWORD)


async def staff_headers(login, org_a, label):
    return await login(f"{label}@{org_a.organization.code}.example.com", STAFF_PASSWORD)


async def a_shelf_and_item(client: AsyncClient, headers):
    shelf = await client.post(
        "/api/v1/inventory/locations",
        headers=headers,
        json={"code": "MAIN", "name": "Store cupboard", "is_default": True},
    )
    assert shelf.status_code == 201, shelf.text
    item = await client.post(
        "/api/v1/inventory/items",
        headers=headers,
        json={"code": "COMP-A2", "name": "Composite A2", "unit": "box", "reorder_level": "2"},
    )
    assert item.status_code == 201, item.text
    return shelf.json(), item.json()


class TestWhoMayReachIt:
    async def test_the_owner_may_maintain_the_catalogue(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        _shelf, item = await a_shelf_and_item(client, headers)
        assert item["unit"] == "box"
        listed = await client.get("/api/v1/inventory/items", headers=headers)
        assert listed.status_code == 200
        assert [row["code"] for row in listed.json()] == ["COMP-A2"]

    async def test_the_cashier_is_offered_nothing(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A cashier holds no `inventory.*` permission at all."""
        headers = await staff_headers(login, org_a, "cashier")
        assert (await client.get("/api/v1/inventory/items", headers=headers)).status_code == 403
        assert (await client.get("/api/v1/inventory/stock", headers=headers)).status_code == 403

    async def test_the_auditor_reads_but_changes_nothing(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "auditor")
        assert (await client.get("/api/v1/inventory/items", headers=headers)).status_code == 200
        refused = await client.post(
            "/api/v1/inventory/items",
            headers=headers,
            json={"code": "X", "name": "X", "unit": "box"},
        )
        assert refused.status_code == 403

    async def test_the_accountant_reads_stock_but_does_not_keep_it(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "accountant")
        assert (await client.get("/api/v1/inventory/stock", headers=headers)).status_code == 200
        refused = await client.post(
            "/api/v1/inventory/locations",
            headers=headers,
            json={"code": "X", "name": "X"},
        )
        assert refused.status_code == 403

    async def test_a_refusal_names_the_permission(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await staff_headers(login, org_a, "cashier")
        refused = await client.get("/api/v1/inventory/items", headers=headers)
        assert "inventory.item.read" in refused.text


class TestStockOnlyArrivesThroughADelivery:
    async def test_there_is_no_endpoint_that_creates_a_receipt(self) -> None:
        """The absence is the control, so it is asserted rather than assumed."""
        from app.main import app

        paths = set(app.openapi()["paths"])
        assert "/api/v1/inventory/receipts" not in paths
        assert "/api/v1/inventory/movements" in paths
        movement_verbs = set(app.openapi()["paths"]["/api/v1/inventory/movements"])
        assert movement_verbs == {"get"}


class TestTheRoundTrip:
    async def test_a_material_consumed_leaves_the_shelf(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        shelf, item = await a_shelf_and_item(client, headers)

        # Stock has to arrive through purchasing, so a shelf starts empty and
        # consuming from it is refused rather than driving it negative.
        refused = await client.post(
            "/api/v1/inventory/consumption",
            headers=headers,
            json={"item_id": item["id"], "quantity": "1", "location_id": shelf["id"]},
        )
        assert refused.status_code == 409, refused.text
        assert "not that much" in refused.text

    async def test_low_stock_reports_an_item_nobody_has_bought_yet(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        await a_shelf_and_item(client, headers)
        low = await client.get("/api/v1/inventory/stock/low", headers=headers)
        assert low.status_code == 200
        assert [row["code"] for row in low.json()] == ["COMP-A2"]

    async def test_a_quantity_sent_as_a_float_is_refused(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A float reaches a stock count as 2.9999999999999996."""
        headers = await owner_headers(login, org_a)
        shelf, item = await a_shelf_and_item(client, headers)
        refused = await client.post(
            "/api/v1/inventory/consumption",
            headers={**headers, "Content-Type": "application/json"},
            content=(
                '{"item_id": "' + item["id"] + '", "quantity": 2.9, '
                '"location_id": "' + shelf["id"] + '"}'
            ),
        )
        assert refused.status_code == 422, refused.text


class TestValuationIsHonest:
    async def test_an_undeclared_method_returns_quantities_and_no_value(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """The clinic can see what it has without the system inventing what
        it is worth."""
        headers = await owner_headers(login, org_a)
        await a_shelf_and_item(client, headers)
        valued = await client.get("/api/v1/inventory/valuation", headers=headers)
        assert valued.status_code == 200, valued.text
        body = valued.json()
        assert body["method"] == "UNDECLARED"
        assert body["total_value"] is None
        assert all(row["unit_cost"] is None for row in body["rows"])


class TestADeliveryReachesTheShelfOverHttp:
    """The gap a browser pass found, kept shut by a test at the same layer.

    The service put stock on a shelf correctly from the first day, and every
    service test passed — but the HTTP schema dropped `item_id`, so a
    delivery recorded through the API reached no shelf at all. Nothing below
    this layer could have caught it.
    """

    async def test_a_receipt_naming_a_material_puts_it_on_the_shelf(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        shelf, item = await a_shelf_and_item(client, headers)
        supplier = await client.post(
            "/api/v1/purchasing/suppliers", headers=headers, json={"name": "Depot"}
        )
        assert supplier.status_code == 201, supplier.text

        receipt = await client.post(
            "/api/v1/purchasing/receipts",
            headers=headers,
            json={
                "supplier_id": supplier.json()["id"],
                "lines": [
                    {
                        "description": "Composite A2",
                        "quantity": "10",
                        "unit": "box",
                        "item_id": item["id"],
                        "location_id": shelf["id"],
                    }
                ],
            },
        )
        assert receipt.status_code == 201, receipt.text

        stock = await client.get(
            f"/api/v1/inventory/stock?item_id={item['id']}", headers=headers
        )
        assert stock.status_code == 200
        rows = stock.json()
        assert len(rows) == 1
        assert Decimal(rows[0]["quantity"]) == Decimal("10")

    async def test_a_receipt_naming_no_material_shelves_nothing(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await owner_headers(login, org_a)
        await a_shelf_and_item(client, headers)
        supplier = await client.post(
            "/api/v1/purchasing/suppliers", headers=headers, json={"name": "Depot"}
        )
        receipt = await client.post(
            "/api/v1/purchasing/receipts",
            headers=headers,
            json={
                "supplier_id": supplier.json()["id"],
                "lines": [{"description": "Cupboard repair", "quantity": "1"}],
            },
        )
        assert receipt.status_code == 201, receipt.text
        stock = await client.get("/api/v1/inventory/stock", headers=headers)
        assert stock.json() == []
