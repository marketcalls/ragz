from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import get_settings
from ragz.core.errors import (
    ConflictError,
    NotFoundError,
    OrgResourceQuotaExceeded,
    WorkspaceAccessDenied,
)
from ragz.core.storage import build_storage
from ragz.modules.audit.service import record_audit
from ragz.modules.documents import folders as folders_service
from ragz.modules.documents.models import Document
from ragz.modules.documents.uploads import UploadedContent
from ragz.modules.outbox import service as outbox_service
from ragz.modules.retrieval import service as retrieval_service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Group
from ragz.modules.tenancy.reembed_models import ReembedJob
from ragz.modules.tenancy.service import get_workspace_checked

log = structlog.get_logger()


async def _enforce_org_upload_quota(
    session: AsyncSession, org_id: UUID, new_file_bytes: int
) -> None:
    """sec RAGZ-PUB-03 (bounded slice): per-org document-count + storage-byte
    caps, checked BEFORE the new document row is created or its bytes are
    stored (fail fast -- a rejected upload must never orphan a MinIO object or
    a partial DB row). Either setting at 0 (the default) disables that
    dimension's check, so a fresh install/dev/test stays unbounded exactly as
    before this existed.

    Counts/sums every Document row currently owned by the org: a row mid
    status="deleting" still occupies real Postgres/object-storage space until
    the worker's delete task actually removes it, so it is intentionally
    included -- conservative (never under-counts), never exploitable via a
    slow delete. A new VERSION of an existing document is just another row
    with its own bytes and is counted the same as any other upload --
    versioning is not exempted from either cap; keeping one rule for every
    row is simpler and cannot be worked around by re-uploading under the same
    filename.
    """
    settings = get_settings()
    if settings.org_max_documents <= 0 and settings.org_max_storage_bytes <= 0:
        return
    count, total_bytes = (
        await session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.size_bytes), 0),
            ).where(Document.org_id == org_id)
        )
    ).one()
    if settings.org_max_documents > 0 and count >= settings.org_max_documents:
        raise OrgResourceQuotaExceeded(
            f"organization document limit reached ({settings.org_max_documents} documents)"
        )
    if (
        settings.org_max_storage_bytes > 0
        and total_bytes + new_file_bytes > settings.org_max_storage_bytes
    ):
        raise OrgResourceQuotaExceeded(
            f"organization storage limit reached ({settings.org_max_storage_bytes} bytes)"
        )


async def create_from_upload(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    *,
    filename: str,
    mime: str,
    data: bytes | UploadedContent,
    folder_id: UUID | None = None,
) -> Document:
    # One internal path. Callers that already hold the bytes (tests, inline bot
    # attachments) keep passing them; the upload route passes an
    # UploadedContent whose payload is still on disk, so a 100 MB upload is
    # never resident. Everything below reads size and digest off the value
    # object rather than off a bytes blob.
    content = data if isinstance(data, UploadedContent) else UploadedContent.from_bytes(data)
    ws = await get_workspace_checked(session, ctx, workspace_id)
    if folder_id is not None:
        await folders_service.get_folder_checked(session, ctx, folder_id, workspace_id=ws.id)
    await _enforce_org_upload_quota(session, ctx.org_id, content.size_bytes)
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
    content_hash = content.sha256
    dup = (
        await session.execute(
            select(Document).where(
                Document.workspace_id == ws.id, Document.content_hash == content_hash
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError(f"identical content already uploaded as document {dup.id}")
    # Folder-scoped predecessor match (this task): the SAME filename in a
    # DIFFERENT folder is an unrelated document with its own lineage --
    # Document.folder_id == folder_id correctly matches NULL == NULL for
    # root-level uploads via SQLAlchemy's `==` operator.
    predecessor = (
        await session.execute(
            select(Document)
            .where(
                Document.workspace_id == ws.id,
                Document.folder_id == folder_id,
                Document.filename == filename,
            )
            .order_by(Document.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    doc = Document(
        org_id=ctx.org_id, workspace_id=ws.id, filename=filename, mime=mime,
        size_bytes=content.size_bytes, content_hash=content_hash, storage_key="",
        created_by=ctx.user_id, folder_id=folder_id,
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
    await storage.put_stream(doc.storage_key, content.stream, content_type=mime)
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.uploaded", target_type="document",
                       target_id=str(doc.id))
    # Transactional outbox (review P1): the document row, its audit event and
    # the intent to ingest it commit TOGETHER. The route used to commit here and
    # then call enqueue_ingest -- a crash or broker outage in that gap left a
    # document stuck at "queued" forever with nothing to retry from, because a
    # status column is not a queue.
    outbox_service.publish(
        session,
        topic="documents.ingest",
        payload={"document_id": str(doc.id), "size_bytes": doc.size_bytes},
    )
    await session.commit()
    return doc


async def list_documents(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID,
    folder_id: UUID | None = None,
) -> list[Document]:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    stmt = (
        select(Document)
        .where(Document.workspace_id == ws.id, Document.folder_id == folder_id)
        .order_by(Document.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


def user_can_access_document(ctx: TenantContext, doc: Document) -> bool:
    """The allow-rule get_document_checked enforces, in-memory (for callers
    that already hold the Document rows, e.g. folder cascade delete --
    folders.py's delete_folder).

    RBAC-05: only an explicit documents.acl.bypass grant sees every restricted
    document. A 'user'-tier caller needs workspace membership AND (unrestricted
    OR an ACL-group intersection). admin/superadmin keep their existing
    UNCONDITIONAL workspace-membership bypass (out of this finding's scope --
    RBAC-05 targets the document-ACL bypass specifically, not general org-admin
    workspace access) but now need the SAME ACL-group relationship as anyone
    else for a RESTRICTED document, unless they hold documents.acl.bypass."""
    if ctx.role == "user":
        if doc.workspace_id not in ctx.workspace_ids:
            return False
        return doc.acl_group_ids is None or bool(set(doc.acl_group_ids) & ctx.group_ids)
    if doc.acl_group_ids is None:
        return True
    return "documents.acl.bypass" in ctx.permissions or bool(
        set(doc.acl_group_ids) & ctx.group_ids
    )


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
    if not user_can_access_document(ctx, doc):
        # Same non-leaking error whether the gate is workspace membership or
        # ACL-group intersection: restricted existence must be
        # indistinguishable from absence (RBAC-5).
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


async def move_document(
    session: AsyncSession, ctx: TenantContext, document_id: UUID, folder_id: UUID | None
) -> Document:
    """Moves a document to a different folder (or to root, folder_id=None).
    Never touches lineage_id/version/supersedes_document_id -- a document's
    version history stays with it regardless of which folder it currently
    sits in; only a NEW upload with a matching filename in the document's
    CURRENT folder continues that lineage going forward (create_from_upload's
    folder-scoped predecessor match)."""
    doc = await get_document_checked(session, ctx, document_id)
    if folder_id is not None:
        await folders_service.get_folder_checked(
            session, ctx, folder_id, workspace_id=doc.workspace_id
        )
    doc.folder_id = folder_id
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.moved", target_type="document", target_id=str(doc.id))
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
    # Fail-closed ACL projection (review P0). The new ACL, the revision bump and
    # the audit event land in ONE commit. From this instant the document is
    # unprojected, so retrieval stops serving it (see unprojected_document_ids)
    # -- the previous ordering left the OLD, broader payload searchable if the
    # Qdrant call below failed, which is precisely the over-grant window.
    #
    # The bump is done IN SQL, not as `doc.security_revision += 1` (Cubic P1).
    # Read-modify-write on this session's snapshot lets two concurrent updates
    # both read revision N and both write N+1; their projections then both
    # compare-and-set successfully against N+1, and Qdrant can end up holding
    # one ACL while Postgres reports the other as projected. Postgres computes
    # the increment from the CURRENT row, so concurrent updates always get
    # distinct revisions.
    await session.execute(
        sa_update(Document)
        .where(Document.id == doc.id)
        .values(
            security_revision=Document.security_revision + 1,
            index_state="pending",
        )
    )
    await session.refresh(doc, ["security_revision", "index_state"])
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="document.acl_changed", target_type="document",
                       target_id=str(doc.id))
    await session.commit()
    await project_document_security(session, doc)
    return doc


async def project_document_security(session: AsyncSession, doc: Document) -> None:
    """Push a document's committed ACL into the vector store and mark it active.

    Idempotent by construction: it writes the CURRENT Postgres ACL and then
    records the revision it projected, so re-running after a partial failure
    converges rather than double-applying. Safe to call from the request path,
    from a retry, or from the reconciler.

    On failure the document stays out of retrieval (index_state='failed') and
    the exception propagates so the caller can surface it -- but unlike before,
    the failure is now merely an availability problem, not a security one.
    """
    target_revision = doc.security_revision
    try:
        collection_name = await retrieval_service.resolve_collection_name(
            session, doc.workspace_id
        )
        await retrieval_service.update_document_acl(
            doc.org_id, doc.id, doc.acl_group_ids, collection_name=collection_name
        )
    except Exception:
        doc.index_state = "failed"
        await session.commit()
        raise
    # Activate with a COMPARE-AND-SET, not an in-memory check. `doc` is this
    # session's snapshot: a concurrent transaction can bump security_revision
    # in the database while we project, and comparing against the stale
    # attribute would mark the row active at an already-superseded revision --
    # re-opening the exact over-grant window this protocol exists to close.
    # Guarding inside the UPDATE lets Postgres arbitrate: if the revision moved,
    # zero rows match, the document stays unprojected, and the reconciler
    # re-drives it at the newer revision.
    result: Any = await session.execute(
        sa_update(Document)
        .where(
            Document.id == doc.id,
            Document.security_revision == target_revision,
        )
        .values(
            projected_security_revision=target_revision,
            index_state="active",
        )
    )
    if result.rowcount == 0:
        # Superseded mid-projection. Record what we DID project so the
        # reconciler can see the gap, but leave the row unprojected.
        await session.execute(
            sa_update(Document)
            .where(Document.id == doc.id)
            .values(projected_security_revision=target_revision, index_state="pending")
        )
        log.info(
            "security_projection_superseded",
            document_id=str(doc.id),
            projected_revision=target_revision,
        )
    await session.commit()
    await session.refresh(doc)


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
    if needs_reindex is not None:
        # Published HERE, not by the route (Cubic P1). promote_lineage has
        # already committed the promotion, so a route-side publish committed
        # separately -- a crash in between left the newly-current version
        # without vectors and no event to recover from. This lands in
        # promote_lineage's own transaction boundary instead.
        outbox_service.publish(
            session,
            topic="documents.reindex",
            payload={"document_id": str(needs_reindex)},
            queue="interactive",
        )
        await session.commit()
    await session.refresh(doc)
    return doc, needs_reindex


async def unprojected_document_ids(
    session: AsyncSession, org_id: UUID, workspace_id: UUID
) -> frozenset[UUID]:
    """Documents whose committed security state has not reached the vector store.

    Fail-closed ACL projection (review P0): a security change commits to
    Postgres before it is projected into Qdrant, so between those two points the
    stored payload still carries the PREVIOUS, possibly broader ACL. Retrieval
    must not serve from that payload -- see retrieval.service._tenant_filter,
    which excludes these ids inside the vector query rather than post-filtering.

    Public because `retrieval` may call another module's service but never reach
    into its ORM models. Backed by the ix_documents_unprojected partial index,
    so this is a small read even on a large corpus: only documents actually
    mid-projection are ever returned.
    """
    rows = await session.execute(
        select(Document.id).where(
            Document.org_id == org_id,
            Document.workspace_id == workspace_id,
            Document.index_state != "active",
        )
    )
    return frozenset(rows.scalars())
