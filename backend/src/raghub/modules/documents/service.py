import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.core.storage import build_storage
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Document
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.service import get_workspace_checked


async def create_from_upload(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    *,
    filename: str,
    mime: str,
    data: bytes,
) -> Document:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    content_hash = hashlib.sha256(data).hexdigest()
    dup = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == ws.id, Document.content_hash == content_hash
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError(f"identical content already uploaded as document {dup.id}")
    doc = Document(
        org_id=ctx.org_id, workspace_id=ws.id, filename=filename, mime=mime,
        size_bytes=len(data), content_hash=content_hash, storage_key="",
        created_by=ctx.user_id,
    )
    session.add(doc)
    await session.flush()  # assigns doc.id for the storage key
    doc.storage_key = f"{ctx.org_id}/{ws.id}/{doc.id}/{filename}"
    storage = build_storage(get_settings())
    await storage.ensure_bucket()
    await storage.put(doc.storage_key, data, content_type=mime)
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.uploaded", target_type="document",
                       target_id=str(doc.id))
    await session.commit()
    return doc


async def list_documents(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[Document]:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    stmt = (
        select(Document)
        .where(Document.workspace_id == ws.id)
        .order_by(Document.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_document_checked(
    session: AsyncSession, ctx: TenantContext, document_id: UUID
) -> Document:
    doc = (
        await session.execute(
            select(Document).where(Document.id == document_id, Document.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFoundError("document not found")
    if ctx.role == "user" and doc.workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return doc


async def set_pinned(
    session: AsyncSession, ctx: TenantContext, document_id: UUID, pinned: bool
) -> Document:
    doc = await get_document_checked(session, ctx, document_id)
    doc.pinned = pinned
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.pinned" if pinned else "document.unpinned",
                       target_type="document", target_id=str(doc.id))
    await session.commit()
    return doc


async def list_pinned_documents(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[Document]:
    """Pinned docs that are actually retrievable (indexed). Ordered oldest-first
    so pinned-source markers are stable across turns."""
    ws = await get_workspace_checked(session, ctx, workspace_id)
    stmt = (
        select(Document)
        .where(Document.workspace_id == ws.id, Document.pinned.is_(True),
               Document.status == "indexed")
        .order_by(Document.created_at)
    )
    return list((await session.execute(stmt)).scalars())
