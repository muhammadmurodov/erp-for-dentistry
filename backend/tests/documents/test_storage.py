"""The filesystem layer: paths that cannot escape, writes that cannot tear.

These tests use a temporary directory rather than the configured store, so a
test run never writes into a real clinic's documents.
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

from app.core.errors import NotFound, ValidationFailed
from app.modules.document.services.storage import DocumentStorage, sha256_hex

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
DOC = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def storage(tmp_path) -> DocumentStorage:
    return DocumentStorage(root=tmp_path)


def a_key(storage: DocumentStorage) -> str:
    return storage.storage_key(organization_id=ORG, document_id=DOC, on=date(2026, 11, 9))


def test_the_key_is_built_only_from_identifiers_we_generated(storage):
    key = a_key(storage)
    assert key == f"{ORG}/2026/11/{DOC}"


def test_a_written_file_is_private_and_has_no_extension(storage):
    key = a_key(storage)
    storage.write(storage_key=key, content=b"%PDF-1.4 hello")

    path = storage.root / key
    assert path.is_file()
    assert path.suffix == ""
    # Owner-only: nothing else on the host reads a clinic's documents.
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(path.parent).st_mode & 0o777) == "0o700"


def test_writing_reports_the_checksum_and_size_actually_stored(storage):
    content = b"%PDF-1.4 contract"
    blob = storage.write(storage_key=a_key(storage), content=content)

    assert blob.checksum_sha256 == sha256_hex(content)
    assert blob.byte_size == len(content)


def test_a_key_that_climbs_out_of_the_root_is_refused(storage):
    with pytest.raises(ValidationFailed):
        storage.write(storage_key="../escaped", content=b"x")


def test_reading_a_file_that_was_never_written_says_so(storage):
    with pytest.raises(NotFound):
        storage.read(storage_key=a_key(storage), expected_checksum="0" * 64)


def test_bytes_that_changed_on_disk_are_not_served(storage):
    key = a_key(storage)
    blob = storage.write(storage_key=key, content=b"%PDF-1.4 original")

    (storage.root / key).write_bytes(b"%PDF-1.4 tampered")

    with pytest.raises(ValidationFailed) as refused:
        storage.read(storage_key=key, expected_checksum=blob.checksum_sha256)
    assert "checksum" in str(refused.value).lower()


def test_verify_all_separates_corrupted_from_missing(storage):
    good = storage.write(storage_key=f"{ORG}/2026/11/{uuid.uuid4()}", content=b"good")
    bad = storage.write(storage_key=f"{ORG}/2026/11/{uuid.uuid4()}", content=b"bad")
    (storage.root / bad.storage_key).write_bytes(b"changed")
    gone = f"{ORG}/2026/11/{uuid.uuid4()}"

    result = storage.verify_all(
        {
            good.storage_key: good.checksum_sha256,
            bad.storage_key: bad.checksum_sha256,
            gone: "0" * 64,
        }
    )

    assert result.checked == 3
    assert result.intact == 1
    assert result.corrupted == [bad.storage_key]
    assert result.missing == [gone]
    assert result.ok is False


def test_a_restored_file_verifies_again(storage):
    """The restore test, in miniature: back up, damage, restore, verify."""
    key = a_key(storage)
    blob = storage.write(storage_key=key, content=b"%PDF-1.4 evidence")
    backup = (storage.root / key).read_bytes()

    (storage.root / key).write_bytes(b"%PDF-1.4 damaged")
    assert storage.verify(storage_key=key, expected_checksum=blob.checksum_sha256) is False

    (storage.root / key).write_bytes(backup)
    assert storage.verify(storage_key=key, expected_checksum=blob.checksum_sha256) is True


def test_a_file_nothing_claims_is_reported_and_left_alone(storage):
    known = storage.write(storage_key=f"{ORG}/2026/11/{uuid.uuid4()}", content=b"kept")
    stray = storage.root / str(ORG) / "2026" / "11" / "not-in-the-database"
    stray.write_bytes(b"orphan")

    orphans = storage.orphans({known.storage_key})

    assert orphans == [stray.relative_to(storage.root).as_posix()]
    # Reported, never removed.
    assert stray.is_file()


def test_a_partial_write_leaves_no_document_behind(storage, monkeypatch):
    """A crash mid-write must not leave a file that looks complete."""
    key = a_key(storage)

    def explode(*args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        storage.write(storage_key=key, content=b"%PDF-1.4 half")

    assert not (storage.root / key).exists()
    # Nor does the temporary file survive.
    assert list((storage.root / str(ORG) / "2026" / "11").glob("*")) == []


def test_an_empty_file_is_refused(storage):
    with pytest.raises(ValidationFailed):
        storage.write(storage_key=a_key(storage), content=b"")
