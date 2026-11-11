"""A stored document: its bytes on disk, everything else in the database.

**The file content never enters a column.** The database holds the metadata,
the SHA-256 checksum and the link to the business record; the bytes live on a
protected filesystem path that only this process writes. The split is what
makes a backup verifiable — the checksum in the database is the statement
that a restored file is the file that was uploaded, and `app.cli
verify-documents` is how that statement is checked.

**The stored path is never built from anything a user typed.** It is derived
from the organization and the document's own identifier, so an uploaded
filename of `../../etc/passwd` is a label on a row and nothing more.

**Nothing here is destroyed.** `DocumentStatus` has no deleted member: how
long a clinic must keep a signed contract is `OPEN_QUESTIONS.md` D1 and is
unanswered, so a document is archived with a reason and keeps its bytes. A
system that quietly destroys what it was given is worse than one that keeps
too much.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.document.enums import (
    DocumentCategory,
    DocumentEntityType,
    DocumentStatus,
)


def _enum(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One file somebody uploaded, and what it is about."""

    __tablename__ = "document"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        _enum(DocumentCategory, "document_category"), nullable=False
    )
    #: What a person would call this document. Required, because a drawer of
    #: `scan_0012.pdf` is not a filing system.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    #: The name the file arrived with. Kept for the person who recognises it,
    #: used for the download filename, and never used to build a path.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Lowercase hex SHA-256 of the bytes as stored. The whole point of this
    #: column is to be compared against the file again later.
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Path relative to the configured storage root. Derived, never typed.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)

    #: The business record this is filed against. Both columns are null
    #: together: a document may belong to the organization at large.
    entity_type: Mapped[str | None] = mapped_column(
        _enum(DocumentEntityType, "document_entity_type")
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    status: Mapped[str] = mapped_column(
        _enum(DocumentStatus, "document_status"),
        nullable=False,
        default=DocumentStatus.STORED.value,
        server_default=DocumentStatus.STORED.value,
    )
    #: Why it was archived. Required when archiving, so a withdrawn document
    #: always says who withdrew it and why.
    status_reason: Mapped[str | None] = mapped_column(Text)

    #: A newer document that replaces this one. The newer row points at the
    #: older, so a chain reads in the direction it was written.
    replaces_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("document.id", ondelete="RESTRICT")
    )

    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_document_organization_id_id"),
        # One file, one row. Re-uploading the identical bytes against the
        # identical record is a double-click, not a second document.
        UniqueConstraint(
            "organization_id",
            "checksum_sha256",
            "entity_type",
            "entity_id",
            name="uq_document_checksum_per_entity",
        ),
        UniqueConstraint("storage_key", name="uq_document_storage_key"),
        CheckConstraint(
            "(entity_type IS NULL) = (entity_id IS NULL)",
            name="ck_document_entity_pair",
        ),
        CheckConstraint("byte_size > 0", name="ck_document_byte_size_positive"),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_checksum_is_hex",
        ),
        CheckConstraint(
            "status <> 'ARCHIVED' OR status_reason IS NOT NULL",
            name="ck_document_archived_has_reason",
        ),
        Index("ix_document_entity", "organization_id", "entity_type", "entity_id"),
        Index("ix_document_category", "organization_id", "category"),
    )


class DocumentAccessLog(Base, UUIDPrimaryKeyMixin):
    """Who read which document, and when.

    Separate from the audit log on purpose. A download is the one event in
    this system that happens often, matters, and would otherwise drown the
    audit trail: a clinic that opens forty scans a day would bury a denied
    permission under them within a week. Both are written — the audit log
    gets the act, this table gets the detail and can be pruned on its own
    schedule once D1 says what that schedule is.
    """

    __tablename__ = "document_access_log"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("document.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    #: `download`, or `checksum_failed` when the bytes on disk stopped
    #: matching what the database says they should be.
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
