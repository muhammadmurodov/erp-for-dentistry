"""Request and response shapes for the document module.

A response never carries the storage key. Where a file sits on disk is this
process's business, and a client that knew the key would be a client that
could try to ask for one.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.document.enums import (
    DocumentCategory,
    DocumentEntityType,
    DocumentStatus,
)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: DocumentCategory
    title: str
    description: str | None
    original_filename: str
    content_type: str
    byte_size: int
    checksum_sha256: str
    entity_type: DocumentEntityType | None
    entity_id: uuid.UUID | None
    status: DocumentStatus
    status_reason: str | None
    replaces_document_id: uuid.UUID | None
    uploaded_by_user_id: uuid.UUID
    created_at: datetime
    #: Filled by the router from the user directory, so a list reads as
    #: "uploaded by Demo Owner" rather than a UUID.
    uploaded_by_name: str | None = None


class ArchiveDocumentRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class StoreVerificationResponse(BaseModel):
    """The answer to "is every document still the document we stored?"."""

    checked: int
    intact: int
    corrupted: list[str]
    missing: list[str]
    ok: bool


class DocumentCapabilitiesResponse(BaseModel):
    """What this install will accept, so the screen can say so before an upload fails."""

    max_upload_bytes: int
    accepted_content_types: list[str]
    categories: list[DocumentCategory]
    entity_types: list[DocumentEntityType]
