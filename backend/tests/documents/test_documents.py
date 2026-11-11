"""Filing a document: what it may be, what it may be filed against, and
what happens to it afterwards.

The rules worth protecting here are the ones a filing cabinet is normally
lost to. A document filed against another clinic's patient is invisible to
both. A file whose type was never checked is a file somebody will eventually
open. And a document store that is never verified is a backup nobody has
read back.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.document.enums import (
    DocumentCategory,
    DocumentEntityType,
    DocumentStatus,
)
from app.modules.document.services.documents import DocumentService, UploadRequest
from app.modules.document.services.storage import DocumentStorage

A_PDF = b"%PDF-1.4\nthe signed agreement\n"
A_PNG = b"\x89PNG\r\n\x1a\nthe radiograph"


@pytest.fixture
def documents(db, audit, tmp_path) -> DocumentService:
    """A service writing into a temporary store, never the configured one."""
    return DocumentService(db, audit, storage=DocumentStorage(root=tmp_path))


def an_upload(org, **overrides) -> UploadRequest:
    defaults = {
        "organization_id": org.organization.id,
        "actor_user_id": org.owner.id,
        "category": DocumentCategory.CONTRACT,
        "title": "Lease agreement 2026",
        "original_filename": "lease.pdf",
        "content_type": "application/pdf",
        "content": A_PDF,
    }
    return UploadRequest(**{**defaults, **overrides})


async def a_patient(patients, org):
    patient, _ = await patients.register(
        organization_id=org.organization.id,
        family_name="Testov",
        given_name="Test",
        actor_user_id=org.owner.id,
    )
    return patient


# --------------------------------------------------------------- uploading


async def test_a_document_keeps_the_checksum_of_what_was_stored(documents, org_a):
    document = await documents.upload(an_upload(org_a))

    import hashlib

    assert document.checksum_sha256 == hashlib.sha256(A_PDF).hexdigest()
    assert document.byte_size == len(A_PDF)
    assert document.status == DocumentStatus.STORED.value
    # Round-tripping proves the stored bytes are the uploaded bytes.
    _, content = await documents.download(
        organization_id=org_a.organization.id,
        document_id=document.id,
        actor_user_id=org_a.owner.id,
    )
    assert content == A_PDF


async def test_the_stored_path_is_never_the_name_the_file_arrived_with(documents, org_a):
    document = await documents.upload(
        an_upload(org_a, original_filename="../../../../etc/passwd.pdf")
    )

    assert "etc" not in document.storage_key
    assert document.storage_key.endswith(str(document.id))
    # The label is kept for the person reading it, with the path stripped.
    assert document.original_filename == "passwd.pdf"


async def test_a_type_the_clinic_does_not_file_is_refused(documents, org_a):
    with pytest.raises(ValidationFailed) as refused:
        await documents.upload(
            an_upload(
                org_a,
                original_filename="script.sh",
                content_type="application/x-sh",
                content=b"#!/bin/sh\n",
            )
        )
    assert "accepted" in refused.value.details


async def test_a_file_that_lies_about_its_type_is_refused(documents, org_a):
    """An HTML page renamed `.pdf` matches no signature, so it must not pass."""
    with pytest.raises(ValidationFailed) as refused:
        await documents.upload(an_upload(org_a, content=b"<svg onload=alert(1)></svg>"))
    assert "do not match" in str(refused.value)


async def test_the_extension_must_agree_with_the_declared_type(documents, org_a):
    with pytest.raises(ValidationFailed):
        await documents.upload(an_upload(org_a, original_filename="lease.png"))


async def test_an_empty_file_is_refused(documents, org_a):
    with pytest.raises(ValidationFailed):
        await documents.upload(an_upload(org_a, content=b""))


async def test_a_file_larger_than_the_limit_is_refused(documents, org_a, monkeypatch):
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "document_max_upload_mb", 1, raising=False)
    oversized = A_PDF + b"x" * (2 * 1024 * 1024)

    with pytest.raises(ValidationFailed) as refused:
        await documents.upload(an_upload(org_a, content=oversized))
    assert "limit_bytes" in refused.value.details


async def test_the_same_file_is_not_filed_twice_in_the_same_place(documents, org_a):
    await documents.upload(an_upload(org_a))

    with pytest.raises(Conflict) as refused:
        await documents.upload(an_upload(org_a, title="Same lease, again"))
    assert "already filed" in str(refused.value)


async def test_the_same_file_may_be_filed_against_two_different_records(documents, org_a, patients):
    """One scan can legitimately belong to two patients' records."""
    one = await a_patient(patients, org_a)
    two, _ = await patients.register(
        organization_id=org_a.organization.id,
        family_name="Ikkinchi",
        given_name="Bemor",
        actor_user_id=org_a.owner.id,
        allow_duplicate=True,
    )

    first = await documents.upload(
        an_upload(
            org_a,
            category=DocumentCategory.CLINICAL,
            original_filename="scan.png",
            content_type="image/png",
            content=A_PNG,
            entity_type=DocumentEntityType.PATIENT,
            entity_id=one.id,
        )
    )
    second = await documents.upload(
        an_upload(
            org_a,
            title="Same scan, other record",
            category=DocumentCategory.CLINICAL,
            original_filename="scan.png",
            content_type="image/png",
            content=A_PNG,
            entity_type=DocumentEntityType.PATIENT,
            entity_id=two.id,
        )
    )

    assert first.id != second.id
    assert first.checksum_sha256 == second.checksum_sha256


# ------------------------------------------------------------- attachment


async def test_a_document_can_be_filed_against_a_patient(documents, org_a, patients):
    patient = await a_patient(patients, org_a)

    document = await documents.upload(
        an_upload(
            org_a,
            category=DocumentCategory.CLINICAL,
            original_filename="scan.png",
            content_type="image/png",
            content=A_PNG,
            entity_type=DocumentEntityType.PATIENT,
            entity_id=patient.id,
        )
    )

    assert document.entity_type == DocumentEntityType.PATIENT.value
    assert document.entity_id == patient.id
    filed = await documents.list(
        organization_id=org_a.organization.id,
        entity_type=DocumentEntityType.PATIENT,
        entity_id=patient.id,
    )
    assert [d.id for d in filed] == [document.id]


async def test_a_record_that_does_not_exist_cannot_be_filed_against(documents, org_a):
    with pytest.raises(ValidationFailed) as refused:
        await documents.upload(
            an_upload(
                org_a,
                entity_type=DocumentEntityType.PATIENT,
                entity_id=uuid.uuid4(),
            )
        )
    assert refused.value.details["entity_type"] == "PATIENT"


async def test_another_organizations_record_cannot_be_filed_against(
    documents, org_a, org_b, patients
):
    """The check is the constraint: there is no foreign key to do this."""
    theirs, _ = await patients.register(
        organization_id=org_b.organization.id,
        family_name="Boshqa",
        given_name="Bemor",
        actor_user_id=org_b.owner.id,
    )

    with pytest.raises(ValidationFailed):
        await documents.upload(
            an_upload(
                org_a,
                entity_type=DocumentEntityType.PATIENT,
                entity_id=theirs.id,
            )
        )


async def test_naming_a_record_type_without_a_record_is_refused(documents, org_a):
    with pytest.raises(ValidationFailed):
        await documents.upload(
            an_upload(org_a, entity_type=DocumentEntityType.PATIENT, entity_id=None)
        )


async def test_a_document_of_another_organization_is_not_found(documents, org_a, org_b):
    theirs = await documents.upload(an_upload(org_b))

    with pytest.raises(NotFound):
        await documents.get(organization_id=org_a.organization.id, document_id=theirs.id)


# ------------------------------------------------------------- superseding


async def test_replacing_a_document_keeps_the_one_it_replaced(documents, org_a):
    original = await documents.upload(an_upload(org_a))

    replacement = await documents.upload(
        an_upload(
            org_a,
            title="Lease agreement 2026, signed",
            content=A_PDF + b"signature\n",
            replaces_document_id=original.id,
        )
    )

    assert replacement.replaces_document_id == original.id
    assert original.status == DocumentStatus.SUPERSEDED.value
    # The superseded one drops out of the working list but keeps its bytes.
    current = await documents.list(organization_id=org_a.organization.id)
    assert [d.id for d in current] == [replacement.id]
    _, content = await documents.download(
        organization_id=org_a.organization.id,
        document_id=original.id,
        actor_user_id=org_a.owner.id,
    )
    assert content == A_PDF


async def test_a_document_cannot_be_replaced_twice(documents, org_a):
    original = await documents.upload(an_upload(org_a))
    await documents.upload(
        an_upload(
            org_a,
            title="First replacement",
            content=A_PDF + b"a",
            replaces_document_id=original.id,
        )
    )

    with pytest.raises(Conflict):
        await documents.upload(
            an_upload(
                org_a,
                title="Second replacement",
                content=A_PDF + b"b",
                replaces_document_id=original.id,
            )
        )


# --------------------------------------------------------------- archiving


async def test_archiving_needs_a_reason_and_destroys_nothing(documents, org_a, tmp_path):
    document = await documents.upload(an_upload(org_a))
    stored = tmp_path / document.storage_key

    with pytest.raises(ValidationFailed):
        await documents.archive(
            organization_id=org_a.organization.id, document_id=document.id, reason="   "
        )

    archived = await documents.archive(
        organization_id=org_a.organization.id,
        document_id=document.id,
        reason="Superseded by the 2027 lease",
    )

    assert archived.status == DocumentStatus.ARCHIVED.value
    assert archived.status_reason == "Superseded by the 2027 lease"
    # The bytes are still there. Retention is OPEN_QUESTIONS D1.
    assert stored.is_file()


async def test_an_archived_document_is_hidden_until_asked_for(documents, org_a):
    document = await documents.upload(an_upload(org_a))
    await documents.archive(
        organization_id=org_a.organization.id,
        document_id=document.id,
        reason="Filed in error",
    )

    assert await documents.list(organization_id=org_a.organization.id) == []
    hidden = await documents.list(organization_id=org_a.organization.id, include_hidden=True)
    assert [d.id for d in hidden] == [document.id]


async def test_an_archived_document_can_be_put_back(documents, org_a):
    document = await documents.upload(an_upload(org_a))
    await documents.archive(
        organization_id=org_a.organization.id,
        document_id=document.id,
        reason="Filed in error",
    )

    restored = await documents.restore(
        organization_id=org_a.organization.id, document_id=document.id
    )

    assert restored.status == DocumentStatus.STORED.value
    assert restored.status_reason is None


async def test_only_an_archived_document_can_be_restored(documents, org_a):
    document = await documents.upload(an_upload(org_a))

    with pytest.raises(Conflict):
        await documents.restore(organization_id=org_a.organization.id, document_id=document.id)


# --------------------------------------------------------------- integrity


async def test_a_document_whose_bytes_changed_is_not_served(documents, org_a, tmp_path):
    document = await documents.upload(an_upload(org_a))
    (tmp_path / document.storage_key).write_bytes(b"%PDF-1.4 tampered")

    with pytest.raises(ValidationFailed) as refused:
        await documents.download(
            organization_id=org_a.organization.id,
            document_id=document.id,
            actor_user_id=org_a.owner.id,
        )
    assert "checksum" in str(refused.value).lower()


async def test_verifying_the_store_finds_damage_and_a_restore_clears_it(documents, org_a, tmp_path):
    document = await documents.upload(an_upload(org_a))
    path = tmp_path / document.storage_key
    original = path.read_bytes()

    assert (await documents.verify_store()).ok is True

    path.write_bytes(b"%PDF-1.4 damaged")
    damaged = await documents.verify_store()
    assert damaged.ok is False
    assert damaged.corrupted == [document.storage_key]

    path.write_bytes(original)
    assert (await documents.verify_store()).ok is True


async def test_a_missing_file_is_reported_rather_than_silently_skipped(documents, org_a, tmp_path):
    document = await documents.upload(an_upload(org_a))
    (tmp_path / document.storage_key).unlink()

    result = await documents.verify_store()

    assert result.missing == [document.storage_key]
    assert result.ok is False


# ------------------------------------------------------------------- audit


async def test_uploading_and_downloading_are_both_recorded(documents, db, org_a):
    document = await documents.upload(an_upload(org_a))
    await documents.download(
        organization_id=org_a.organization.id,
        document_id=document.id,
        actor_user_id=org_a.owner.id,
    )
    await db.flush()

    actions = set(
        (await db.execute(select(AuditLog.action).where(AuditLog.target_id == str(document.id))))
        .scalars()
        .all()
    )

    assert events.DOCUMENT_UPLOADED in actions
    assert events.DOCUMENT_DOWNLOADED in actions


async def test_searching_matches_the_title(documents, org_a):
    await documents.upload(an_upload(org_a, title="Lease agreement 2026"))
    await documents.upload(
        an_upload(
            org_a,
            title="Insurance policy",
            original_filename="insurance.pdf",
            content=A_PDF + b"x",
        )
    )

    found = await documents.list(organization_id=org_a.organization.id, search="lease")

    assert [d.title for d in found] == ["Lease agreement 2026"]


async def test_searching_also_matches_the_name_the_file_arrived_with(documents, org_a):
    """The person looking often remembers the file, not the title they typed."""
    await documents.upload(
        an_upload(org_a, title="Scanned on the 9th", original_filename="ijara-2026.pdf")
    )

    found = await documents.list(organization_id=org_a.organization.id, search="ijara")

    assert [d.title for d in found] == ["Scanned on the 9th"]
