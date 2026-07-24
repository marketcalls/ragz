from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import get_settings
from raghub.core.errors import PayloadTooLarge
from raghub.modules.documents import folders as folders_service
from raghub.modules.documents import metadata as metadata_service
from raghub.modules.documents import service
from raghub.modules.documents.models import Document
from raghub.modules.documents.schemas import (
    AclUpdate,
    ApprovedPatch,
    DocumentOut,
    DocumentPatch,
    FolderCreate,
    FolderOut,
    FolderPatch,
    MetadataFieldCreate,
    MetadataFieldOut,
    MetadataValuesIn,
)
from raghub.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_permission,
    require_role,
)
from raghub.worker.tasks import enqueue_delete, enqueue_ingest, enqueue_reindex

router = APIRouter(tags=["documents"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
# Task 13 (RBAC-2): granular guards layered ON TOP of (not instead of) the
# workspace-membership/ACL checks inside the service layer -- get_workspace_checked
# etc. still run unconditionally.
UploadDep = Annotated[TenantContext, Depends(require_permission("documents.upload"))]
DeleteDep = Annotated[TenantContext, Depends(require_permission("documents.delete"))]
ConfigureDep = Annotated[TenantContext, Depends(require_permission("workspace.configure"))]


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
    enqueue_ingest(doc.id, doc.size_bytes)
    return _serialize_document(doc, ctx)


@router.get("/workspaces/{workspace_id}/documents", response_model=list[DocumentOut])
async def list_workspace_documents(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep,
    folder_id: UUID | None = None,
) -> list[DocumentOut]:
    docs = await service.list_documents(session, ctx, workspace_id, folder_id)
    return [_serialize_document(d, ctx) for d in docs]


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


@router.patch("/documents/{document_id}", response_model=DocumentOut)
async def patch_document(
    document_id: UUID, body: DocumentPatch, session: SessionDep, ctx: CtxDep
) -> DocumentOut:
    doc = await service.get_document_checked(session, ctx, document_id)
    if "pinned" in body.model_fields_set and body.pinned is not None:
        doc = await service.set_pinned(session, ctx, document_id, body.pinned)
    if "folder_id" in body.model_fields_set:
        doc = await service.move_document(session, ctx, document_id, body.folder_id)
    return _serialize_document(doc, ctx)


@router.put("/documents/{document_id}/acl", response_model=DocumentOut)
async def set_document_acl(
    document_id: UUID, body: AclUpdate, session: SessionDep, ctx: AdminDep
) -> DocumentOut:
    doc = await service.set_document_acl(session, ctx, document_id, body.acl_group_ids)
    return _serialize_document(doc, ctx)


@router.put("/documents/{document_id}/approved", response_model=DocumentOut)
async def set_document_approved(
    document_id: UUID, body: ApprovedPatch, session: SessionDep, ctx: AdminDep
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
    workspace_id: UUID, body: FolderCreate, session: SessionDep, ctx: UploadDep
) -> FolderOut:
    folder = await folders_service.create_folder(
        session, ctx, workspace_id, name=body.name, parent_folder_id=body.parent_folder_id
    )
    return FolderOut.model_validate(folder)


@router.get("/workspaces/{workspace_id}/folders", response_model=list[FolderOut])
async def list_folders(workspace_id: UUID, session: SessionDep, ctx: CtxDep) -> list[FolderOut]:
    return [
        FolderOut.model_validate(f)
        for f in await folders_service.list_folders(session, ctx, workspace_id)
    ]


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def patch_folder(
    folder_id: UUID, body: FolderPatch, session: SessionDep, ctx: UploadDep
) -> FolderOut:
    folder = await folders_service.rename_or_move_folder(
        session, ctx, folder_id, name=body.name, parent_folder_id=body.parent_folder_id,
        fields_set=body.model_fields_set,
    )
    return FolderOut.model_validate(folder)


# (DELETE /folders/{folder_id} is added in Task 3, once delete_folder exists.)


# DOC-6: metadata schema (fields) + values. Task 13 moves field CRUD to a
# "workspace.configure" permission and the value PUT to "documents.upload"
# (declarative permission checks, not inline role checks).
# GET is member-gated (CtxDep): the filter bar and per-doc Tags dialog need the
# field list for ANY workspace member, not just admins. list_fields already
# runs get_workspace_checked, which fences org + membership for role=user.
@router.get("/workspaces/{workspace_id}/metadata-fields", response_model=list[MetadataFieldOut])
async def list_metadata_fields(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep
) -> list[MetadataFieldOut]:
    fields = await metadata_service.list_fields(session, ctx, workspace_id)
    return [MetadataFieldOut.model_validate(f) for f in fields]


@router.post(
    "/workspaces/{workspace_id}/metadata-fields", status_code=201, response_model=MetadataFieldOut
)
async def create_metadata_field(
    workspace_id: UUID, body: MetadataFieldCreate, session: SessionDep, ctx: ConfigureDep
) -> MetadataFieldOut:
    field = await metadata_service.create_field(
        session, ctx, workspace_id,
        name=body.name, label=body.label, field_type=body.field_type, options=body.options,
    )
    return MetadataFieldOut.model_validate(field)


@router.delete("/metadata-fields/{field_id}", status_code=204)
async def delete_metadata_field(field_id: UUID, session: SessionDep, ctx: ConfigureDep) -> None:
    await metadata_service.delete_field(session, ctx, field_id)


@router.put("/documents/{document_id}/metadata", response_model=DocumentOut)
async def set_document_metadata(
    document_id: UUID, body: MetadataValuesIn, session: SessionDep, ctx: UploadDep
) -> DocumentOut:
    doc = await metadata_service.set_document_metadata(session, ctx, document_id, body.values)
    return _serialize_document(doc, ctx)
