"""Filing a document, finding it again, and withdrawing it.

The rules this service holds, and why each one is here rather than in the
router:

* **A document is filed against a record that exists, in this organization.**
  One column cannot carry eleven foreign keys, so the check is done here,
  against `ATTACHABLE_TABLES`, before anything is written. Without it, a
  document filed against another clinic's patient identifier would be
  invisible to both and discoverable by neither.

* **What may be uploaded is an allowlist, not a denylist.** A denylist is a
  list of the attacks somebody thought of. The allowlist is the paper a
  clinic actually files.

* **The bytes are written before the row is committed, and the row is what
  makes the file real.** A rolled-back transaction can leave an orphan on
  disk, which `app.cli verify-documents` reports; the reverse — a row whose
  file never landed — would be a download that fails forever, so the write
  happens first on purpose.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import get_logger
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.document.enums import (
    ATTACHABLE_TABLES,
    HIDDEN_STATUSES,
    DocumentCategory,
    DocumentEntityType,
    DocumentStatus,
)
from app.modules.document.models import Document, DocumentAccessLog
from app.modules.document.services.storage import DocumentStorage, sha256_hex

log = get_logger(__name__)

#: What a clinic files, by the media type a browser actually sends.
#:
#: SVG is deliberately absent although it is an image: it is a document
#: format that can carry script, and this store serves bytes back to a
#: browser. Office macro formats (`.xlsm`, `.docm`) are absent for the same
#: reason — the one macro-enabled workbook this project reads is handled by
#: the banking importer, which parses it and never hands it back.
ALLOWED_CONTENT_TYPES: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/tiff": (".tif", ".tiff"),
    "image/webp": (".webp",),
    "text/plain": (".txt",),
    "text/csv": (".csv",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (".pptx",),
}

#: What the first bytes of a file must be, for every declared type that has a
#: recognisable signature.
#:
#: This is an allowlist in the same spirit as the one above: the declared type
#: must be one this clinic files, *and* the bytes must actually look like it.
#: Requiring the signature rather than merely forbidding a contradictory one
#: is the difference that matters — an HTML page renamed `.pdf` matches no
#: signature at all, so a "reject known mismatches" check waves it through.
#:
#: It is not the only defence. Nothing stored here is ever executed, and
#: downloads go out as an attachment with `nosniff`, so a file that lied
#: about its type would still be inert. This stops it being stored in the
#: first place, which is where a wrong file is cheapest to catch.
_REQUIRED_MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    # Little-endian and big-endian TIFF respectively.
    "image/tiff": (b"II*\x00", b"MM\x00*"),
    # RIFF container; the `WEBP` marker sits at offset 8 and is checked below.
    "image/webp": (b"RIFF",),
    # Every OOXML file is a zip. `PK\x05\x06` is an empty archive, which is
    # not a document but is a valid zip, so it is not accepted here.
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04",),
}

#: Types with no signature to check. Both are text, and text is what it is.
#: They are listed explicitly rather than falling through a default, so
#: adding a format to the allowlist forces a decision about its signature.
_SIGNATURELESS: frozenset[str] = frozenset({"text/plain", "text/csv"})


@dataclass(frozen=True, slots=True)
class UploadRequest:
    organization_id: uuid.UUID
    actor_user_id: uuid.UUID
    category: DocumentCategory
    title: str
    original_filename: str
    content_type: str
    content: bytes
    description: str | None = None
    entity_type: DocumentEntityType | None = None
    entity_id: uuid.UUID | None = None
    replaces_document_id: uuid.UUID | None = None


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditRecorder,
        storage: DocumentStorage | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._storage = storage or DocumentStorage()

    @property
    def storage(self) -> DocumentStorage:
        return self._storage

    # ------------------------------------------------------------ uploading

    async def upload(self, request: UploadRequest) -> Document:
        title = request.title.strip()
        if not title:
            raise ValidationFailed("Give the document a title somebody could search for")

        content_type = self._check_content_type(
            content_type=request.content_type,
            filename=request.original_filename,
            content=request.content,
        )
        self._check_size(request.content)

        if (request.entity_type is None) != (request.entity_id is None):
            raise ValidationFailed(
                "Name both the kind of record and which one, or neither",
                details={
                    "entity_type": request.entity_type,
                    "entity_id": str(request.entity_id) if request.entity_id else None,
                },
            )
        if request.entity_type is not None and request.entity_id is not None:
            await self._check_entity_exists(
                organization_id=request.organization_id,
                entity_type=request.entity_type,
                entity_id=request.entity_id,
            )

        replaced: Document | None = None
        if request.replaces_document_id is not None:
            replaced = await self.get(
                organization_id=request.organization_id,
                document_id=request.replaces_document_id,
            )
            if replaced.status == DocumentStatus.SUPERSEDED.value:
                raise Conflict(
                    "That document has already been replaced by a newer one",
                    details={"document_id": str(replaced.id)},
                )

        checksum = self._storage_checksum(request.content)
        await self._refuse_duplicate(
            organization_id=request.organization_id,
            checksum=checksum,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
        )

        document_id = uuid.uuid4()
        storage_key = self._storage.storage_key(
            organization_id=request.organization_id,
            document_id=document_id,
            on=date.today(),
        )
        blob = self._storage.write(storage_key=storage_key, content=request.content)

        document = Document(
            id=document_id,
            organization_id=request.organization_id,
            category=request.category.value,
            title=title,
            description=(request.description or "").strip() or None,
            original_filename=self._safe_label(request.original_filename),
            content_type=content_type,
            byte_size=blob.byte_size,
            checksum_sha256=blob.checksum_sha256,
            storage_key=blob.storage_key,
            entity_type=request.entity_type.value if request.entity_type else None,
            entity_id=request.entity_id,
            status=DocumentStatus.STORED.value,
            replaces_document_id=replaced.id if replaced else None,
            uploaded_by_user_id=request.actor_user_id,
        )
        self._session.add(document)

        if replaced is not None:
            replaced.status = DocumentStatus.SUPERSEDED.value
            replaced.status_reason = f"Replaced by {title}"

        await self._session.flush()
        self._audit.record(
            events.DOCUMENT_UPLOADED,
            target_type="document",
            target_id=document.id,
            target_label=title,
            new_values={
                "category": document.category,
                "entity_type": document.entity_type,
                "entity_id": str(document.entity_id) if document.entity_id else None,
                "byte_size": document.byte_size,
                "checksum_sha256": document.checksum_sha256,
                "replaces_document_id": (str(replaced.id) if replaced else None),
            },
        )
        return document

    # -------------------------------------------------------------- reading

    async def get(self, *, organization_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = await self._session.get(Document, document_id)
        if document is None or document.organization_id != organization_id:
            raise NotFound("Document not found", details={"document_id": str(document_id)})
        return document

    async def list(
        self,
        *,
        organization_id: uuid.UUID,
        category: DocumentCategory | None = None,
        entity_type: DocumentEntityType | None = None,
        entity_id: uuid.UUID | None = None,
        include_hidden: bool = False,
        search: str | None = None,
    ) -> list[Document]:
        stmt = select(Document).where(Document.organization_id == organization_id)
        if category is not None:
            stmt = stmt.where(Document.category == category.value)
        if entity_type is not None:
            stmt = stmt.where(Document.entity_type == entity_type.value)
        if entity_id is not None:
            stmt = stmt.where(Document.entity_id == entity_id)
        if not include_hidden:
            stmt = stmt.where(Document.status.not_in([s.value for s in HIDDEN_STATUSES]))
        if search:
            needle = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                func.lower(Document.title).like(needle)
                | func.lower(Document.original_filename).like(needle)
            )
        stmt = stmt.order_by(Document.created_at.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def download(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> tuple[Document, bytes]:
        """Hand back the bytes, having proved they are still the right bytes.

        A failed checksum is recorded before the error is raised, because the
        request is about to fail and the fact that a stored document stopped
        matching its checksum is exactly the thing somebody needs to find
        later.
        """
        document = await self.get(organization_id=organization_id, document_id=document_id)
        try:
            content = self._storage.read(
                storage_key=document.storage_key,
                expected_checksum=document.checksum_sha256,
            )
        except (ValidationFailed, NotFound):
            # Written in its own transaction. This request is about to fail
            # and its transaction will roll back, but a document that stopped
            # matching its checksum is precisely the event somebody needs to
            # find afterwards — the same reasoning as a denied permission in
            # `app/api/deps.py`.
            await self._record_integrity_failure(
                organization_id=organization_id,
                document_id=document.id,
                document_title=document.title,
                actor_user_id=actor_user_id,
                ip_address=ip_address,
            )
            raise

        self._session.add(
            DocumentAccessLog(
                organization_id=organization_id,
                document_id=document.id,
                user_id=actor_user_id,
                action="download",
                ip_address=ip_address,
            )
        )
        self._audit.record(
            events.DOCUMENT_DOWNLOADED,
            target_type="document",
            target_id=document.id,
            target_label=document.title,
        )
        return document, content

    async def _record_integrity_failure(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        document_title: str,
        actor_user_id: uuid.UUID,
        ip_address: str | None,
    ) -> None:
        """Record a failed integrity check so it survives the failed request.

        Never lets a logging failure mask the original error: the caller is
        about to raise the thing the user actually needs to see.
        """
        try:
            factory = get_session_factory()
            async with factory() as session:
                session.add(
                    DocumentAccessLog(
                        organization_id=organization_id,
                        document_id=document_id,
                        user_id=actor_user_id,
                        action="checksum_failed",
                        ip_address=ip_address,
                    )
                )
                AuditRecorder(
                    session,
                    actor_user_id=actor_user_id,
                    actor_label=self._audit.actor_label,
                    organization_id=organization_id,
                    ip_address=ip_address,
                ).record(
                    events.DOCUMENT_INTEGRITY_FAILED,
                    outcome="failure",
                    target_type="document",
                    target_id=document_id,
                    target_label=document_title,
                    reason="stored bytes do not match the recorded checksum",
                )
                await session.commit()
        except Exception:  # pragma: no cover - must not mask the real error
            log.exception("document.integrity_record_failed", document_id=str(document_id))

    # ------------------------------------------------------------ archiving

    async def archive(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        reason: str,
    ) -> Document:
        """Withdraw a document from the working list. The bytes stay.

        There is no counterpart that destroys the file. Retention is
        `OPEN_QUESTIONS.md` D1 and nobody has answered it, so this system
        does not decide when a clinic's signed contract may stop existing.
        """
        reason = reason.strip()
        if not reason:
            raise ValidationFailed("Say why this document is being archived")

        document = await self.get(organization_id=organization_id, document_id=document_id)
        if document.status == DocumentStatus.ARCHIVED.value:
            raise Conflict(
                "That document is already archived",
                details={"document_id": str(document_id)},
            )
        previous = document.status
        document.status = DocumentStatus.ARCHIVED.value
        document.status_reason = reason
        await self._session.flush()
        self._audit.record(
            events.DOCUMENT_ARCHIVED,
            target_type="document",
            target_id=document.id,
            target_label=document.title,
            reason=reason,
            old_values={"status": previous},
            new_values={"status": document.status},
        )
        return document

    async def restore(self, *, organization_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        """Put an archived document back on the working list."""
        document = await self.get(organization_id=organization_id, document_id=document_id)
        if document.status != DocumentStatus.ARCHIVED.value:
            raise Conflict(
                "Only an archived document can be restored",
                details={"status": document.status},
            )
        document.status = DocumentStatus.STORED.value
        document.status_reason = None
        await self._session.flush()
        self._audit.record(
            events.DOCUMENT_RESTORED,
            target_type="document",
            target_id=document.id,
            target_label=document.title,
        )
        return document

    # ----------------------------------------------------------- integrity

    async def verify_store(self, *, organization_id: uuid.UUID | None = None):
        """Re-checksum every stored document against the database."""
        stmt = select(Document.storage_key, Document.checksum_sha256)
        if organization_id is not None:
            stmt = stmt.where(Document.organization_id == organization_id)
        expected = dict((await self._session.execute(stmt)).all())
        return self._storage.verify_all(expected)

    async def known_storage_keys(self) -> set[str]:
        rows = await self._session.execute(select(Document.storage_key))
        return set(rows.scalars().all())

    # ------------------------------------------------------------- internal

    def _storage_checksum(self, content: bytes) -> str:
        return sha256_hex(content)

    def _check_size(self, content: bytes) -> None:
        limit = get_settings().document_max_upload_bytes
        if not content:
            raise ValidationFailed("The uploaded file is empty")
        if len(content) > limit:
            raise ValidationFailed(
                f"The file is larger than {limit // (1024 * 1024)} MB",
                details={"byte_size": len(content), "limit_bytes": limit},
            )

    def _check_content_type(self, *, content_type: str, filename: str, content: bytes) -> str:
        declared = (content_type or "").split(";")[0].strip().lower()
        if declared not in ALLOWED_CONTENT_TYPES:
            raise ValidationFailed(
                "That file type cannot be filed here",
                details={
                    "content_type": declared or None,
                    "accepted": sorted(ALLOWED_CONTENT_TYPES),
                },
            )
        suffixes = ALLOWED_CONTENT_TYPES[declared]
        if not filename.lower().endswith(suffixes):
            raise ValidationFailed(
                "The file name does not match the kind of file it says it is",
                details={"filename": filename, "expected_suffix": list(suffixes)},
            )
        self._check_signature(declared=declared, content=content)
        return declared

    @staticmethod
    def _check_signature(*, declared: str, content: bytes) -> None:
        """Refuse a file whose bytes do not look like what it claims to be."""
        if declared in _SIGNATURELESS:
            return
        magics = _REQUIRED_MAGIC.get(declared)
        if magics is None:  # pragma: no cover - a type added without a decision
            raise ValidationFailed(
                "That file type cannot be filed here",
                details={"content_type": declared},
            )
        if not any(content.startswith(magic) for magic in magics):
            raise ValidationFailed(
                "The file's contents do not match the type it was sent as",
                details={"content_type": declared},
            )
        if declared == "image/webp" and content[8:12] != b"WEBP":
            raise ValidationFailed(
                "The file's contents do not match the type it was sent as",
                details={"content_type": declared},
            )

    @staticmethod
    def _safe_label(filename: str) -> str:
        """Keep the name for a person to read; strip anything path-shaped.

        The stored path never comes from here, so this is about what is shown
        on a screen and sent back in a download header — not about where the
        file lands.
        """
        cleaned = (filename or "document").replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = "".join(c for c in cleaned if c.isprintable() and c not in '"\r\n')
        return cleaned.strip()[:255] or "document"

    async def _check_entity_exists(
        self,
        *,
        organization_id: uuid.UUID,
        entity_type: DocumentEntityType,
        entity_id: uuid.UUID,
    ) -> None:
        table = ATTACHABLE_TABLES[entity_type]
        # The table name comes from a dictionary keyed by an enum member, so
        # it can only ever be one of eleven literals written in this
        # repository. The identifiers are bound parameters.
        stmt = text(
            f"SELECT 1 FROM {table} WHERE id = :entity_id AND organization_id = :organization_id"
        )
        found = (
            await self._session.execute(
                stmt, {"entity_id": entity_id, "organization_id": organization_id}
            )
        ).first()
        if found is None:
            raise ValidationFailed(
                "There is no such record to file this against",
                details={"entity_type": entity_type.value, "entity_id": str(entity_id)},
            )

    async def _refuse_duplicate(
        self,
        *,
        organization_id: uuid.UUID,
        checksum: str,
        entity_type: DocumentEntityType | None,
        entity_id: uuid.UUID | None,
    ) -> None:
        stmt = select(Document.id, Document.title).where(
            Document.organization_id == organization_id,
            Document.checksum_sha256 == checksum,
            Document.entity_type == (entity_type.value if entity_type else None),
            Document.entity_id == entity_id,
        )
        existing = (await self._session.execute(stmt)).first()
        if existing is not None:
            raise Conflict(
                "This exact file is already filed here",
                details={"document_id": str(existing[0]), "title": existing[1]},
            )
