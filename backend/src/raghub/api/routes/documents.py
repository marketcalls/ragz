from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import get_settings
from raghub.core.errors import PayloadTooLarge
from raghub.modules.documents import service
from raghub.modules.documents.schemas import DocumentOut
from raghub.modules.tenancy.context import TenantContext, get_tenant_context
from raghub.worker.tasks import enqueue_delete, enqueue_ingest

router = APIRouter(tags=["documents"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post("/workspaces/{workspace_id}/documents", status_code=201,
             response_model=DocumentOut)
async def upload_document(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    data = await file.read()
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"file exceeds {get_settings().max_upload_mb} MB limit")
    doc = await service.create_from_upload(
        session, ctx, workspace_id,
        filename=file.filename or "upload.bin",
        mime=file.content_type or "application/octet-stream",
        data=data,
    )
    enqueue_ingest(doc.id, doc.size_bytes)
    return DocumentOut.model_validate(doc)


@router.get("/workspaces/{workspace_id}/documents", response_model=list[DocumentOut])
async def list_workspace_documents(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep
) -> list[DocumentOut]:
    docs = await service.list_documents(session, ctx, workspace_id)
    return [DocumentOut.model_validate(d) for d in docs]


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document(
    document_id: UUID, session: SessionDep, ctx: CtxDep
) -> dict[str, str]:
    doc = await service.get_document_checked(session, ctx, document_id)
    enqueue_delete(doc.id, ctx.user_id)
    return {"status": "deletion scheduled"}
