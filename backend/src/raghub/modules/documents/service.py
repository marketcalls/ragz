import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.core.storage import build_storage
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Document
from raghub.modules.retrieval import service as retrieval_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Group
from raghub.modules.tenancy.reembed_models import ReembedJob
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
    reembed_in_progress = (
        await session.execute(
            select(ReembedJob.id).where(
                ReembedJob.workspace_id == ws.id,
                ReembedJob.started_at.is_not(None),
                ReembedJob.finished_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if reembed_in_progress is not None:
        raise ConflictError(
            "a re-embed job is in progress for this workspace; try again once it completes"
        )
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
    predecessor = (
        await session.execute(
            select(Document)
            .where(Document.workspace_id == ws.id, Document.filename == filename)
            .order_by(Document.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    doc = Document(
        org_id=ctx.org_id, workspace_id=ws.id, filename=filename, mime=mime,
        size_bytes=len(data), content_hash=content_hash, storage_key="",
        created_by=ctx.user_id,
        version=(predecessor.version + 1) if predecessor else 1,
        lineage_id=predecessor.lineage_id if predecessor else uuid4(),  # placeholder, fixed below
        supersedes_document_id=predecessor.id if predecessor else None,
    )
    session.add(doc)
    await session.flush()  # assigns doc.id for the storage key
    if predecessor is None:
        doc.lineage_id = doc.id
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
    if (
        ctx.role == "user"
        and doc.acl_group_ids is not None
        and not set(doc.acl_group_ids) & ctx.group_ids
    ):
        # Same non-leaking error as the workspace gate: restricted existence
        # must be indistinguishable from absence (RBAC-5).
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return doc


async def has_indexed_documents(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> bool:
    """Phase 3 escalation gate: only workspaces that actually HAVE indexed
    content loop on weak retrieval (design §1) — empty workspaces fall
    straight through to the fallback policy."""
    row = (
        await session.execute(
            select(Document.id)
            .where(
                Document.org_id == ctx.org_id,
                Document.workspace_id == workspace_id,
                Document.status == "indexed",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


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


async def set_document_acl(
    session: AsyncSession,
    ctx: TenantContext,
    document_id: UUID,
    acl_group_ids: list[UUID] | None,
) -> Document:
    """Invariant: acl_group_ids is None === unrestricted (every workspace
    member may read the document); a non-empty list restricts reads to those
    groups. `[]` is never a valid value here — AclUpdate rejects it at the
    schema layer (422) precisely because get_document_checked's `is not None`
    gate would otherwise treat a wire-level `[]` as "restricted to no
    groups" (i.e. admins-only), silently different from the "unrestricted"
    the payload shape suggests."""
    doc = await get_document_checked(session, ctx, document_id)
    if acl_group_ids is not None:
        owned = set(
            (
                await session.execute(
                    select(Group.id).where(
                        Group.id.in_(acl_group_ids), Group.org_id == ctx.org_id
                    )
                )
            ).scalars()
        )
        if owned != set(acl_group_ids):
            raise NotFoundError("one or more groups not found")
    doc.acl_group_ids = list(acl_group_ids) if acl_group_ids is not None else None
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.acl_changed", target_type="document",
                       target_id=str(doc.id))
    await session.commit()
    # After commit so a failed Qdrant call never strands a half-applied ACL in
    # PG; on Qdrant failure the route 502s and the admin retries (set_payload
    # is idempotent).
    collection_name = await retrieval_service.resolve_collection_name(session, doc.workspace_id)
    await retrieval_service.update_document_acl(
        ctx.org_id, doc.id, doc.acl_group_ids, collection_name=collection_name
    )
    return doc


async def promote_lineage(session: AsyncSession, org_id: UUID, lineage_id: UUID) -> UUID | None:
    """Converge one lineage on its effective current version (DOC-5).

    effective = highest-version APPROVED indexed row if any indexed row is
    approved, else highest-version indexed row. Exactly one row ends with
    is_current=True. Qdrant holds points only for the effective version:
    promotion flips the winner visible FIRST, then deletes demoted points
    (momentary double-serve beats momentary blackout). A winner whose points
    were deleted earlier (approval flipped back to an old version) is
    re-indexed from its stored chunks.json; promotion re-runs on completion.

    Locking: the row select uses FOR UPDATE so concurrent promotions of the
    same lineage serialize -- a second caller blocks until the first commits
    its is_current flip, then re-reads the now-committed state here and
    re-converges from there. Without this, two versions promoting at once
    could each compute a stale "effective" row and both end up is_current
    (pattern precedent: chat/service.py's add_message, auth/service.py's
    rotate_refresh).

    Returns the id of a document that needs re-indexing (the effective winner
    has no live points), or None once flags have converged and nothing needs
    a reindex. This function never enqueues anything itself (Plan K Task 11,
    carried Plan H TODO): modules/ must never import worker/ (the layering
    rule this was the one narrow exception to), so the decision of WHETHER
    and HOW to enqueue belongs to the entrypoint that called promote_lineage
    -- ingest.py's runner tails (via worker/tasks.py's Celery task wrappers,
    which already define enqueue_reindex in the same module) and set_approved's
    ONE route call site (api/routes/documents.py, which may freely import
    worker.tasks per the layers contract).
    """
    rows = list(
        (
            await session.execute(
                select(Document)
                .where(Document.lineage_id == lineage_id, Document.status == "indexed")
                .with_for_update()
            )
        ).scalars()
    )
    if not rows:
        await session.commit()  # release the (empty) lock scope
        return None
    approved_rows = [r for r in rows if r.approved]
    effective = max(approved_rows or rows, key=lambda d: d.version)

    if not effective.vectors_present:
        # Points were deleted when this version was demoted. Rebuild first;
        # the reindex task ends by calling promote_lineage again. Commit
        # before returning: it releases the FOR UPDATE locks (the reindex
        # task opens its own session and re-runs promotion -- holding the
        # locks here would deadlock an eager worker) and makes the
        # needs-reindex state durable before the caller decides how to act
        # on it.
        await session.commit()
        return effective.id

    for row in rows:
        row.is_current = row.id == effective.id
    await session.commit()

    # DOC-10: every row in a lineage shares the same workspace_id (a document
    # is never reassigned to a different workspace across versions) -- one
    # resolution covers both calls below.
    collection_name = await retrieval_service.resolve_collection_name(
        session, effective.workspace_id
    )
    await retrieval_service.update_document_current(
        org_id, effective.id, is_current=True, collection_name=collection_name
    )
    for row in rows:
        if row.id != effective.id and row.vectors_present:
            await retrieval_service.delete_document_points(
                org_id, row.id, collection_name=collection_name
            )
            row.vectors_present = False
    await session.commit()
    return None


async def set_approved(
    session: AsyncSession, ctx: TenantContext, document_id: UUID, approved: bool
) -> tuple[Document, UUID | None]:
    """Returns (doc, needs_reindex) -- needs_reindex mirrors promote_lineage's
    own return contract (Plan K Task 11): set_approved never enqueues, its ONE
    route call site (api/routes/documents.py) does, since that's the layer
    allowed to import worker.tasks without a layering exception."""
    doc = await get_document_checked(session, ctx, document_id)
    doc.approved = approved
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id,
        action="document.approved" if approved else "document.unapproved",
        target_type="document", target_id=str(doc.id),
    )
    await session.commit()
    needs_reindex = await promote_lineage(session, ctx.org_id, doc.lineage_id)
    await session.refresh(doc)
    return doc, needs_reindex
