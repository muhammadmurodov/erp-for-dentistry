"""Filing cabinet endpoints: upload, find, download, archive, verify.

Two permissions gate everything here, and they already existed before the
module did: `document.read` to see and download, `document.manage` to file
and withdraw. Every role holds the first; the roles that handle paper hold
the second.

A download is audited twice over — once in the audit log as an act, once in
`document_access_log` with the detail. Reading a clinical scan is a privacy
event, and a clinic that cannot say who opened a patient's X-ray has not
protected it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy import select

from app.api.deps import AuditDep, Ctx, DbSession, OrgCtx, require_permissions
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.modules.document.enums import (
    DocumentCategory,
    DocumentEntityType,
)
from app.modules.document.models import Document
from app.modules.document.schemas import (
    ArchiveDocumentRequest,
    DocumentCapabilitiesResponse,
    DocumentResponse,
    StoreVerificationResponse,
)
from app.modules.document.services.documents import (
    ALLOWED_CONTENT_TYPES,
    DocumentService,
    UploadRequest,
)
from app.modules.identity.models import User

router = APIRouter(prefix="/documents", tags=["documents"])


async def _with_uploader_names(db: DbSession, documents: list[Document]) -> list[DocumentResponse]:
    """Resolve uploader identifiers to names in one query, not N."""
    rows = [DocumentResponse.model_validate(d) for d in documents]
    if not rows:
        return rows
    ids = {d.uploaded_by_user_id for d in documents}
    names = dict((await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))).all())
    for row in rows:
        row.uploaded_by_name = names.get(row.uploaded_by_user_id)
    return rows


@router.get(
    "/capabilities",
    response_model=DocumentCapabilitiesResponse,
    dependencies=[Depends(require_permissions("document.read"))],
)
async def capabilities() -> DocumentCapabilitiesResponse:
    """What may be uploaded, so the screen says it before the upload fails."""
    return DocumentCapabilitiesResponse(
        max_upload_bytes=get_settings().document_max_upload_bytes,
        accepted_content_types=sorted(ALLOWED_CONTENT_TYPES),
        categories=list(DocumentCategory),
        entity_types=list(DocumentEntityType),
    )


@router.get(
    "",
    response_model=list[DocumentResponse],
    dependencies=[Depends(require_permissions("document.read"))],
)
async def list_documents(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    category: DocumentCategory | None = Query(default=None),
    entity_type: DocumentEntityType | None = Query(default=None),
    entity_id: uuid.UUID | None = Query(default=None),
    include_hidden: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=100),
) -> list[DocumentResponse]:
    documents = await DocumentService(db, audit).list(
        organization_id=context.require_organization(),
        category=category,
        entity_type=entity_type,
        entity_id=entity_id,
        include_hidden=include_hidden,
        search=search,
    )
    return await _with_uploader_names(db, documents)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("document.manage"))],
)
async def upload_document(
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    file: UploadFile = File(...),
    category: DocumentCategory = Form(...),
    title: str = Form(..., max_length=200),
    description: str | None = Form(default=None),
    entity_type: DocumentEntityType | None = Form(default=None),
    entity_id: uuid.UUID | None = Form(default=None),
    replaces_document_id: uuid.UUID | None = Form(default=None),
) -> DocumentResponse:
    """File a document, optionally against a record it is about.

    The size limit is enforced twice: once by reading one byte past it here,
    so an oversized upload is refused without being held in memory in full,
    and once in the service, which is the boundary a test exercises.
    """
    limit = get_settings().document_max_upload_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ValidationFailed(
            f"The file is larger than {limit // (1024 * 1024)} MB",
            details={"limit_bytes": limit},
        )

    document = await DocumentService(db, audit).upload(
        UploadRequest(
            organization_id=context.require_organization(),
            actor_user_id=context.user_id,
            category=category,
            title=title,
            original_filename=file.filename or "document",
            content_type=file.content_type or "",
            content=content,
            description=description,
            entity_type=entity_type,
            entity_id=entity_id,
            replaces_document_id=replaces_document_id,
        )
    )
    await db.commit()
    await db.refresh(document)
    return (await _with_uploader_names(db, [document]))[0]


@router.get(
    "/verify",
    response_model=StoreVerificationResponse,
    dependencies=[Depends(require_permissions("document.manage"))],
)
async def verify_store(
    context: OrgCtx, db: DbSession, audit: AuditDep
) -> StoreVerificationResponse:
    """Re-checksum this organization's documents against what is on disk.

    This is the restore test. A backup that has never been read back is a
    belief about a disk, and this endpoint is how that belief is checked
    after a restore — or on any day somebody wants to know.
    """
    result = await DocumentService(db, audit).verify_store(
        organization_id=context.require_organization()
    )
    return StoreVerificationResponse(
        checked=result.checked,
        intact=result.intact,
        corrupted=result.corrupted,
        missing=result.missing,
        ok=result.ok,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    dependencies=[Depends(require_permissions("document.read"))],
)
async def get_document(
    document_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> DocumentResponse:
    document = await DocumentService(db, audit).get(
        organization_id=context.require_organization(), document_id=document_id
    )
    return (await _with_uploader_names(db, [document]))[0]


@router.get(
    "/{document_id}/content",
    dependencies=[Depends(require_permissions("document.read"))],
    response_class=Response,
)
async def download_document(
    document_id: uuid.UUID,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
    caller: Ctx,
) -> Response:
    """Stream the bytes back, having checked they are still the right bytes.

    `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`
    together mean a browser saves the file rather than rendering it in the
    application's own origin — which is what stops an uploaded document from
    becoming a script running as the person who opened it.
    """
    service = DocumentService(db, audit)
    document, content = await service.download(
        organization_id=context.require_organization(),
        document_id=document_id,
        actor_user_id=caller.user_id,
        ip_address=caller.ip_address,
    )
    await db.commit()
    filename = document.original_filename.replace('"', "")
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/{document_id}/archive",
    response_model=DocumentResponse,
    dependencies=[Depends(require_permissions("document.manage"))],
)
async def archive_document(
    document_id: uuid.UUID,
    payload: ArchiveDocumentRequest,
    context: OrgCtx,
    db: DbSession,
    audit: AuditDep,
) -> DocumentResponse:
    """Withdraw a document, with a reason. Nothing is destroyed.

    There is no delete endpoint. How long a clinic must keep what it was
    given is `OPEN_QUESTIONS.md` D1, and until that is answered this system
    will not be the thing that decided a contract could stop existing.
    """
    document = await DocumentService(db, audit).archive(
        organization_id=context.require_organization(),
        document_id=document_id,
        reason=payload.reason,
    )
    await db.commit()
    return (await _with_uploader_names(db, [document]))[0]


@router.post(
    "/{document_id}/restore",
    response_model=DocumentResponse,
    dependencies=[Depends(require_permissions("document.manage"))],
)
async def restore_document(
    document_id: uuid.UUID, context: OrgCtx, db: DbSession, audit: AuditDep
) -> DocumentResponse:
    document = await DocumentService(db, audit).restore(
        organization_id=context.require_organization(), document_id=document_id
    )
    await db.commit()
    return (await _with_uploader_names(db, [document]))[0]
