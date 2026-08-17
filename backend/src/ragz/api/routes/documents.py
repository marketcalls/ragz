from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import get_settings
from ragz.core.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLarge,
    WorkspaceAccessDenied,
)
from ragz.core.storage import build_storage
from ragz.modules.documents import folders as folders_service
from ragz.modules.documents import metadata as metadata_service
from ragz.modules.documents import service
from ragz.modules.documents.models import Document
from ragz.modules.documents.schemas import (
    AclUpdate,
    ApprovedPatch,
    DocumentMovePatch,
    DocumentOut,
    DocumentPinPatch,
    EnsurePathRequest,
    FolderCreate,
    FolderDeletePreview,
    FolderOut,
    FolderPatch,
    MetadataFieldCreate,
    MetadataFieldOut,
    MetadataValuesIn,
)
from ragz.modules.tenancy.context import TenantContext, require_action
from ragz.worker.outbox import dispatch_pending
from ragz.worker.tasks import enqueue_delete, enqueue_reindex

router = APIRouter(tags=["documents"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
# Task 13 (RBAC-2): granular guards layered ON TOP of (not instead of) the
# workspace-membership/ACL checks inside the service layer -- get_workspace_checked
# etc. still run unconditionally.
UploadDep = Annotated[TenantContext, Depends(require_action("documents.upload"))]
DeleteDep = Annotated[TenantContext, Depends(require_action("documents.delete"))]
# Per-document Reindex: re-runs the chunk->embed ingest pipeline for one
# document (same mutation class as an upload/ingest -- it (re)writes the
# document's index points), so it reuses the existing WRITE action
# "documents.upload" rather than minting a new permission. That action is held
# by admin/contributor and is NOT in DEFAULT_USER_PERMISSIONS, so a plain
# reader cannot trigger a reindex.
ReindexDep = Annotated[TenantContext, Depends(require_action("documents.upload"))]
# Task 5 (RBAC-03): document listing had no permission gate at all -- any
# authenticated member could list regardless of role-template contents.
ListDep = Annotated[TenantContext, Depends(require_action("documents.list"))]
# Citation-viewer backend: streams the original file bytes. Gated on the
# existing "documents.content.read" action (already in PERMISSIONS and
# DEFAULT_USER_PERMISSIONS) -- the same content-level action a plain member
# needs to have citations answered from a document's chunks, since this
# endpoint serves the identical content, just as raw bytes instead of chunks.
FileReadDep = Annotated[TenantContext, Depends(require_action("documents.content.read"))]
# sec RAGZ-PUB-01: every route below now ENFORCES exactly the action it
# DECLARES in api/policy.py. Previously several of these routes were gated on a
# broader/unrelated action (or auth-only CtxDep), so a custom role denied the
# granular action could still perform it. Pin and move are split into two
# single-action endpoints; folder + metadata-field CRUD each carry their own
# catalog action rather than piggy-backing on documents.upload/workspace.configure.
PinDep = Annotated[TenantContext, Depends(require_action("documents.pin"))]
MoveDep = Annotated[TenantContext, Depends(require_action("documents.move"))]
MetadataUpdateDep = Annotated[TenantContext, Depends(require_action("documents.metadata.update"))]
FolderCreateDep = Annotated[TenantContext, Depends(require_action("folders.create"))]
FolderReadDep = Annotated[TenantContext, Depends(require_action("folders.read"))]
FolderUpdateDep = Annotated[TenantContext, Depends(require_action("folders.update"))]
FolderDeleteDep = Annotated[TenantContext, Depends(require_action("folders.delete"))]
MetadataManageDep = Annotated[TenantContext, Depends(require_action("workspace.metadata.manage"))]
# sec RAGZ-PUB-01b: these two DECLARE documents.acl.manage / documents.approve
# in api/policy.py but were still gated on require_role("admin") (auth-only
# w.r.t. the granular catalog) -- a custom role granted the specific action
# without the "admin" role tier could not use them, and the dependency-graph
# enforcement gate (audit_route_enforcement) flags any route whose declared
# action isn't actually wired via require_action(...).
AclManageDep = Annotated[TenantContext, Depends(require_action("documents.acl.manage"))]
ApproveDep = Annotated[TenantContext, Depends(require_action("documents.approve"))]


def _serialize_document(doc: Document, ctx: TenantContext) -> DocumentOut:
    """`acl_group_ids` is admin-only metadata (CLAUDE.md ACL posture): a
    restricted document still shows up in workspace listings for plain
    members (existence is visible, Drive-style) but the group ids themselves
    are blanked to `null` unless the caller is admin/superadmin. Contents and
    citations remain enforced in the vector query regardless of this field."""
    out = DocumentOut.model_validate(doc)
    if ctx.role not in ("admin", "superadmin"):
        out = out.model_copy(update={"acl_group_ids": None})
    return out


@router.post("/workspaces/{workspace_id}/documents", status_code=201,
             response_model=DocumentOut)
async def upload_document(
    workspace_id: UUID, session: SessionDep, ctx: UploadDep,
    request: Request, file: Annotated[UploadFile, File()],
    folder_id: Annotated[UUID | None, Form()] = None,
) -> DocumentOut:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024

    # Check Content-Length header before reading (with 4KB multipart overhead allowance)
    if content_length := request.headers.get("content-length"):
        try:
            if int(content_length) > max_bytes + 4096:
                raise PayloadTooLarge(f"file exceeds {get_settings().max_upload_mb} MB limit")
        except ValueError:
            pass  # Invalid Content-Length, let chunked read handle it

    # Read file in chunks, aborting early if size exceeds limit
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise PayloadTooLarge(f"file exceeds {get_settings().max_upload_mb} MB limit")

    data = bytes(buf)
    doc = await service.create_from_upload(
        session, ctx, workspace_id,
        filename=file.filename or "upload.bin",
        mime=file.content_type or "application/octet-stream",
        data=data, folder_id=folder_id,
    )
    # The work is already durable: create_from_upload committed an outbox event
    # in the same transaction as the document. This is only a latency nudge so
    # ingestion starts now rather than at the next sweep -- if it fails, or the
    # process dies here, the sweep still picks the event up. That is the whole
    # difference from the old enqueue_ingest call, which WAS the only record
    # that the work was owed.
    await dispatch_pending()
    return _serialize_document(doc, ctx)


@router.get("/workspaces/{workspace_id}/documents", response_model=list[DocumentOut])
async def list_workspace_documents(
    workspace_id: UUID, session: SessionDep, ctx: ListDep,
    folder_id: UUID | None = None,
) -> list[DocumentOut]:
    docs = await service.list_documents(session, ctx, workspace_id, folder_id)
    return [_serialize_document(d, ctx) for d in docs]


@router.get("/documents/{document_id}/file")
async def get_document_file(
    document_id: UUID, session: SessionDep, ctx: FileReadDep
) -> Response:
    """Streams the original uploaded file bytes for the citation viewer.
    ACL-CRITICAL: get_document_checked only gates existence/workspace/org
    membership (Drive-style -- a restricted document still appears in
    listings). It is NOT sufficient on its own to authorize the file's
    CONTENT, so user_can_access_document is re-checked explicitly here, same
    as it is inside get_document_checked -- a plain member who can SEE a
    restricted document in a listing but isn't in its ACL group must get the
    same non-leaking denial as an unknown document, not the bytes."""
    doc = await service.get_document_checked(session, ctx, document_id)
    if not service.user_can_access_document(ctx, doc):
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    storage = build_storage(get_settings())
    try:
        data = await storage.get(doc.storage_key)
    except NotFoundError as exc:
        # The document ROW exists (it just passed get_document_checked) but its
        # original file object is absent from storage -- typically the file was
        # never stored (seeded/imported without the raw bytes) or object storage
        # was reset while Postgres/Qdrant kept their data (retrieval still works
        # from vectors; only the original file is gone). Log the key for the
        # operator (not leaked to the client) and return an ACTIONABLE message
        # distinct from a missing-document 404.
        structlog.get_logger("ragz.documents").warning(
            "document_file_missing_in_storage",
            document_id=str(document_id), storage_key=doc.storage_key,
        )
        raise NotFoundError(
            "The original file is not available in storage. It may need to be "
            "re-uploaded (the document's indexed content is unaffected)."
        ) from exc
    return Response(
        content=data,
        media_type=doc.mime or "application/octet-stream",
        headers={
            # inline (not attachment): the frontend viewer renders it (PDF
            # viewer etc.) rather than the browser force-downloading it.
            "Content-Disposition": f'inline; filename="{doc.filename}"',
        },
    )


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document(
    document_id: UUID, session: SessionDep, ctx: DeleteDep
) -> dict[str, str]:
    doc = await service.get_document_checked(session, ctx, document_id)
    # Flip status before enqueueing so a delete failure is visible: if the
    # worker never picks up the task (or the on_failure hook fires), the
    # document is left in a clearly-broken state rather than silently
    # looking untouched forever.
    doc.status = "deleting"
    await session.commit()
    enqueue_delete(doc.id, ctx.user_id)
    return {"status": "deletion scheduled"}


@router.post("/documents/{document_id}/reindex", status_code=202)
async def reindex_document(
    document_id: UUID, session: SessionDep, ctx: ReindexDep
) -> dict[str, str]:
    """Re-runs the chunk->embed ingest pipeline for a single document
    (enqueue_reindex -> documents.reindex Celery task). This route only
    ENQUEUES: if the document's stored raw file/artifacts are missing the job
    fails at parse like any other ingest failure -- that's surfaced on the
    document's status, not here.

    ACL-CRITICAL (identical posture to get_document_file): a reindex acts on
    the document's CONTENT, so user_can_access_document is re-checked
    explicitly after get_document_checked -- a plain member who can SEE a
    restricted document in a listing but isn't in its ACL group must get the
    same non-leaking denial as an unknown document, never a reindex.
    """
    doc = await service.get_document_checked(session, ctx, document_id)
    if not service.user_can_access_document(ctx, doc):
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    # Only reindex from a settled state: an already-queued/processing document
    # is mid-ingest (a second reindex would race it) and a deleting one is on
    # its way out. indexed (refresh/re-embed) and failed (retry) are the two
    # states where a manual reindex is meaningful.
    if doc.status not in ("indexed", "failed"):
        raise ConflictError(
            "document is not in a reindexable state (must be indexed or failed)"
        )
    enqueue_reindex(doc.id)
    return {"status": "reindexing"}


# sec RAGZ-PUB-01: the former combined PATCH /documents/{id} took only CtxDep
# (auth-only) yet both pinned AND moved a document -- a custom role denied
# documents.pin or documents.move could still do both. Split into two endpoints,
# each declarative-at-boundary (iron rule 4) gated on exactly its own action.
@router.patch("/documents/{document_id}/pin", response_model=DocumentOut)
async def pin_document(
    document_id: UUID, body: DocumentPinPatch, session: SessionDep, ctx: PinDep
) -> DocumentOut:
    doc = await service.set_pinned(session, ctx, document_id, body.pinned)
    return _serialize_document(doc, ctx)


@router.patch("/documents/{document_id}/move", response_model=DocumentOut)
async def move_document_route(
    document_id: UUID, body: DocumentMovePatch, session: SessionDep, ctx: MoveDep
) -> DocumentOut:
    doc = await service.move_document(session, ctx, document_id, body.folder_id)
    return _serialize_document(doc, ctx)


@router.put("/documents/{document_id}/acl", response_model=DocumentOut)
async def set_document_acl(
    document_id: UUID, body: AclUpdate, session: SessionDep, ctx: AclManageDep
) -> DocumentOut:
    doc = await service.set_document_acl(session, ctx, document_id, body.acl_group_ids)
    return _serialize_document(doc, ctx)


@router.put("/documents/{document_id}/approved", response_model=DocumentOut)
async def set_document_approved(
    document_id: UUID, body: ApprovedPatch, session: SessionDep, ctx: ApproveDep
) -> DocumentOut:
    # Plan K Task 11: set_approved returns (doc, needs_reindex) instead of
    # enqueueing itself -- this route is the entrypoint layer allowed to
    # import worker.tasks without a layering exception, so it performs the
    # actual enqueue.
    doc, needs_reindex = await service.set_approved(session, ctx, document_id, body.approved)
    if needs_reindex is not None:
        enqueue_reindex(needs_reindex)
    return _serialize_document(doc, ctx)


@router.post("/workspaces/{workspace_id}/folders", status_code=201, response_model=FolderOut)
async def create_folder(
    workspace_id: UUID, body: FolderCreate, session: SessionDep, ctx: FolderCreateDep
) -> FolderOut:
    folder = await folders_service.create_folder(
        session, ctx, workspace_id, name=body.name, parent_folder_id=body.parent_folder_id
    )
    return FolderOut.model_validate(folder)


@router.post(
    "/workspaces/{workspace_id}/folders/ensure-path", status_code=200, response_model=FolderOut
)
async def ensure_folder_path(
    workspace_id: UUID, body: EnsurePathRequest, session: SessionDep, ctx: FolderCreateDep
) -> FolderOut:
    folder = await folders_service.ensure_path(session, ctx, workspace_id, body.path)
    return FolderOut.model_validate(folder)


@router.get("/workspaces/{workspace_id}/folders", response_model=list[FolderOut])
async def list_folders(
    workspace_id: UUID, session: SessionDep, ctx: FolderReadDep
) -> list[FolderOut]:
    return [
        FolderOut.model_validate(f)
        for f in await folders_service.list_folders(session, ctx, workspace_id)
    ]


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def patch_folder(
    folder_id: UUID, body: FolderPatch, session: SessionDep, ctx: FolderUpdateDep
) -> FolderOut:
    folder = await folders_service.rename_or_move_folder(
        session, ctx, folder_id, name=body.name, parent_folder_id=body.parent_folder_id,
        fields_set=body.model_fields_set,
    )
    return FolderOut.model_validate(folder)


@router.get("/folders/{folder_id}/delete-preview", response_model=FolderDeletePreview)
async def preview_folder_delete(
    folder_id: UUID, session: SessionDep, ctx: FolderDeleteDep
) -> FolderDeletePreview:
    # sec RAGZ-PUB-01: gated behind the same "folders.delete" permission as the
    # actual delete route below (FolderDeleteDep), since this preview only
    # exists to back that delete's confirmation dialog.
    document_count, subfolder_count = await folders_service.count_subtree(
        session, ctx, folder_id
    )
    return FolderDeletePreview(document_count=document_count, subfolder_count=subfolder_count)


@router.delete("/folders/{folder_id}", status_code=202)
async def delete_folder(
    folder_id: UUID, session: SessionDep, ctx: FolderDeleteDep
) -> dict[str, int]:
    # Task 3: folders_service.delete_folder never enqueues itself (modules/
    # must never import worker/, Plan K Task 11's inversion) -- it returns
    # the document ids whose status it already flipped to "deleting"; this
    # route is the entrypoint layer allowed to import worker.tasks, so it
    # performs the actual enqueue_delete call for each one.
    document_ids = await folders_service.delete_folder(session, ctx, folder_id)
    for document_id in document_ids:
        enqueue_delete(document_id, ctx.user_id)
    return {"documents_deleted": len(document_ids)}


# DOC-6: metadata schema (fields) + values. sec RAGZ-PUB-01 aligns every route
# here to the action it declares in api/policy.py: field CRUD is
# "workspace.metadata.manage", the value PUT is "documents.metadata.update", and
# the field listing is "documents.list" (declarative permission checks, not
# inline role checks).
# GET is gated on documents.list (ListDep): the filter bar and per-doc Tags
# dialog need the field list for any member who can already list documents, not
# just admins. list_fields already runs get_workspace_checked, which fences
# org + membership for role=user.
@router.get("/workspaces/{workspace_id}/metadata-fields", response_model=list[MetadataFieldOut])
async def list_metadata_fields(
    workspace_id: UUID, session: SessionDep, ctx: ListDep
) -> list[MetadataFieldOut]:
    fields = await metadata_service.list_fields(session, ctx, workspace_id)
    return [MetadataFieldOut.model_validate(f) for f in fields]


@router.post(
    "/workspaces/{workspace_id}/metadata-fields", status_code=201, response_model=MetadataFieldOut
)
async def create_metadata_field(
    workspace_id: UUID, body: MetadataFieldCreate, session: SessionDep, ctx: MetadataManageDep
) -> MetadataFieldOut:
    field = await metadata_service.create_field(
        session, ctx, workspace_id,
        name=body.name, label=body.label, field_type=body.field_type, options=body.options,
    )
    return MetadataFieldOut.model_validate(field)


@router.delete("/metadata-fields/{field_id}", status_code=204)
async def delete_metadata_field(
    field_id: UUID, session: SessionDep, ctx: MetadataManageDep
) -> None:
    await metadata_service.delete_field(session, ctx, field_id)


@router.put("/documents/{document_id}/metadata", response_model=DocumentOut)
async def set_document_metadata(
    document_id: UUID, body: MetadataValuesIn, session: SessionDep, ctx: MetadataUpdateDep
) -> DocumentOut:
    doc = await metadata_service.set_document_metadata(session, ctx, document_id, body.values)
    return _serialize_document(doc, ctx)
