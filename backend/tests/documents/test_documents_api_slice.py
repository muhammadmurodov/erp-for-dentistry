"""Documents over HTTP: who may file, who may only read, what a download says.

The service tests cover the filing rules. These cover the boundary, and three
things that only exist at this layer:

* there is **no delete endpoint** — archiving is the only way out of the list;
* a download leaves with headers that stop a browser rendering it in the
  application's own origin;
* the store can be verified through the API, which is the restore test a
  person can run without a shell.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"

A_PDF = b"%PDF-1.4\nan agreement\n"


async def owner_headers(login, org_a):
    return await login(f"owner@{org_a.organization.code}.example.com", OWNER_PASSWORD)


async def staff_headers(login, org_a, label):
    return await login(f"{label}@{org_a.organization.code}.example.com", STAFF_PASSWORD)


def a_file(content: bytes = A_PDF, name: str = "lease.pdf", kind: str = "application/pdf"):
    return {"file": (name, content, kind)}


def a_form(**overrides) -> dict[str, str]:
    return {"category": "CONTRACT", "title": "Lease agreement 2026", **overrides}


async def upload(client: AsyncClient, headers, **overrides):
    return await client.post(
        "/api/v1/documents", headers=headers, files=a_file(), data=a_form(**overrides)
    )


@pytest.fixture(autouse=True)
def _store_in_a_temporary_directory(tmp_path, monkeypatch):
    """Never write into the configured store from a test."""
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "document_storage_path", str(tmp_path), raising=False)


async def test_an_owner_can_file_a_document(client: AsyncClient, login, org_a):
    headers = await owner_headers(login, org_a)

    response = await upload(client, headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Lease agreement 2026"
    assert body["uploaded_by_name"]
    # The storage key is this process's business and never leaves it.
    assert "storage_key" not in body


async def test_a_reader_may_look_but_not_file(client: AsyncClient, login, org_a):
    """Every role holds `document.read`; only some hold `document.manage`."""
    reader = await staff_headers(login, org_a, "auditor")

    listing = await client.get("/api/v1/documents", headers=reader)
    refused = await client.post("/api/v1/documents", headers=reader, files=a_file(), data=a_form())

    assert listing.status_code == 200
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"


async def test_a_download_is_sent_as_an_attachment_a_browser_will_not_run(
    client: AsyncClient, login, org_a
):
    headers = await owner_headers(login, org_a)
    document = (await upload(client, headers)).json()

    response = await client.get(f"/api/v1/documents/{document['id']}/content", headers=headers)

    assert response.status_code == 200
    assert response.content == A_PDF
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


async def test_there_is_no_way_to_delete_a_document(client: AsyncClient, login, org_a):
    """Retention is OPEN_QUESTIONS D1. Until it is answered, nothing goes."""
    headers = await owner_headers(login, org_a)
    document = (await upload(client, headers)).json()

    response = await client.delete(f"/api/v1/documents/{document['id']}", headers=headers)

    assert response.status_code == 405


async def test_archiving_needs_a_reason(client: AsyncClient, login, org_a):
    headers = await owner_headers(login, org_a)
    document = (await upload(client, headers)).json()

    refused = await client.post(
        f"/api/v1/documents/{document['id']}/archive", headers=headers, json={"reason": ""}
    )
    accepted = await client.post(
        f"/api/v1/documents/{document['id']}/archive",
        headers=headers,
        json={"reason": "Replaced by the 2027 agreement"},
    )

    assert refused.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ARCHIVED"


async def test_the_screen_is_told_the_limits_before_an_upload_fails(
    client: AsyncClient, login, org_a
):
    headers = await owner_headers(login, org_a)

    response = await client.get("/api/v1/documents/capabilities", headers=headers)

    body = response.json()
    assert body["max_upload_bytes"] > 0
    assert "application/pdf" in body["accepted_content_types"]
    # SVG can carry script and is deliberately absent.
    assert "image/svg+xml" not in body["accepted_content_types"]


async def test_the_store_can_be_verified_through_the_api(client: AsyncClient, login, org_a):
    headers = await owner_headers(login, org_a)
    await upload(client, headers)

    response = await client.get("/api/v1/documents/verify", headers=headers)

    body = response.json()
    assert body["ok"] is True
    assert body["checked"] >= 1
    assert body["corrupted"] == [] and body["missing"] == []


async def test_verifying_the_store_needs_authority_to_manage(client: AsyncClient, login, org_a):
    reader = await staff_headers(login, org_a, "auditor")

    response = await client.get("/api/v1/documents/verify", headers=reader)

    assert response.status_code == 403


async def test_another_organizations_document_is_not_found(
    client: AsyncClient, login, org_a, org_b
):
    theirs = await client.post(
        "/api/v1/documents",
        headers=await login(f"owner@{org_b.organization.code}.example.com", OWNER_PASSWORD),
        files=a_file(),
        data=a_form(),
    )
    assert theirs.status_code == 201

    response = await client.get(
        f"/api/v1/documents/{theirs.json()['id']}",
        headers=await owner_headers(login, org_a),
    )

    assert response.status_code == 404
