"""Folder tree service: create/list/rename/move (this task). Cascade delete
lives in Task 3's delete_folder, added to this same file, since it shares
the subtree-walk helper below. delete_folder never imports worker.tasks --
it returns the document ids the caller (the route) must enqueue_delete on,
same inversion as documents/service.py's promote_lineage/set_approved."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from ragz.modules.audit.service import record_audit
from ragz.modules.documents.models import Document, Folder
from ragz.modules.outbox import service as outbox_service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.service import get_workspace_checked

_MAX_TREE_DEPTH = 1000  # defensive cap on ancestor-chain walks, matching this
# codebase's other defensive caps (e.g. retrieval/service.py's _SCROLL_PAGE_CAP)


async def create_folder(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, *,
    name: str, parent_folder_id: UUID | None,
) -> Folder:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    if parent_folder_id is not None:
        await get_folder_checked(session, ctx, parent_folder_id, workspace_id=ws.id)
    folder = Folder(
        org_id=ctx.org_id, workspace_id=ws.id, parent_folder_id=parent_folder_id,
        name=name, created_by=ctx.user_id,
    )
    session.add(folder)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="folder.created", target_type="folder", target_id=str(folder.id))
    await session.commit()
    return folder


async def ensure_path(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, path: str
) -> Folder:
    """Idempotent bulk path creation for whole-folder-tree drag-and-drop: given
    "Legal/Contracts/2024", get-or-creates each segment under the previous
    one (starting from root) and returns the deepest folder. A concurrent
    double-submit racing to create the same segment hits the unique
    index/constraint on INSERT; caught and re-read rather than propagating,
    since two clients uploading the same tree concurrently must converge on
    ONE set of folders, not error."""
    ws = await get_workspace_checked(session, ctx, workspace_id)
    # Captured as a plain value (not re-accessed via ws.id below): `rollback()`
    # in the except branch expires every ORM object attached to the session,
    # `ws` included, and an expired attribute access outside an explicit
    # `await session.*()` call triggers an implicit sync lazy-load that
    # crashes with MissingGreenlet under the async driver. Every reference to
    # the workspace id after this point must use this plain UUID, never
    # `ws.id` again.
    ws_id = ws.id
    segments = [s for s in path.split("/") if s.strip()]
    if not segments:
        raise ConflictError("path must contain at least one non-empty segment")
    parent_id: UUID | None = None
    folder: Folder | None = None
    for segment in segments:
        existing = (
            await session.execute(
                select(Folder).where(
                    Folder.workspace_id == ws_id, Folder.parent_folder_id == parent_id,
                    Folder.name == segment,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            folder = existing
        else:
            folder = Folder(
                org_id=ctx.org_id, workspace_id=ws_id, parent_folder_id=parent_id,
                name=segment, created_by=ctx.user_id,
            )
            session.add(folder)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                folder = (
                    await session.execute(
                        select(Folder).where(
                            Folder.workspace_id == ws_id, Folder.parent_folder_id == parent_id,
                            Folder.name == segment,
                        )
                    )
                ).scalar_one()  # a concurrent writer must have created it; re-read is safe
        parent_id = folder.id
    assert folder is not None  # segments is non-empty (checked above), so the loop ran >=1 time
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="folder.path_ensured", target_type="folder",
                       target_id=str(folder.id))
    await session.commit()
    return folder


async def list_folders(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[Folder]:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    stmt = select(Folder).where(Folder.workspace_id == ws.id).order_by(Folder.name)
    return list((await session.execute(stmt)).scalars())


async def get_folder_checked(
    session: AsyncSession, ctx: TenantContext, folder_id: UUID, *,
    workspace_id: UUID | None = None,
) -> Folder:
    """workspace_id, when given, additionally asserts the folder belongs to
    THAT workspace (not just the caller's org) -- used when validating a
    parent_folder_id supplied alongside an explicit workspace_id, so a folder
    from a DIFFERENT workspace in the same org can never be nested under."""
    folder = (
        await session.execute(
            select(Folder).where(Folder.id == folder_id, Folder.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if folder is None:
        raise NotFoundError("folder not found")
    if ctx.role == "user" and folder.workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    if workspace_id is not None and folder.workspace_id != workspace_id:
        raise NotFoundError("folder not found")
    return folder


async def _is_ancestor_or_self(
    session: AsyncSession, *, candidate_id: UUID, folder_id: UUID
) -> bool:
    """True if candidate_id is folder_id itself, or any ancestor of it --
    walks UP from folder_id through parent_folder_id, checking for
    candidate_id at each step. Used to reject a move that would nest a
    folder under its own descendant (which would disconnect that subtree
    from the root)."""
    current: UUID | None = folder_id
    for _ in range(_MAX_TREE_DEPTH):
        if current == candidate_id:
            return True
        if current is None:
            return False
        row = (
            await session.execute(select(Folder.parent_folder_id).where(Folder.id == current))
        ).scalar_one_or_none()
        current = row
    return True  # defensive: treat an unexpectedly deep/corrupt chain as unsafe


async def rename_or_move_folder(
    session: AsyncSession, ctx: TenantContext, folder_id: UUID, *,
    name: str | None, parent_folder_id: UUID | None, fields_set: set[str],
) -> Folder:
    """fields_set mirrors WorkspacePatch's model_fields_set convention:
    "parent_folder_id" present in fields_set (even with value None) means
    "move to root," distinct from "not part of this patch at all."""
    folder = await get_folder_checked(session, ctx, folder_id)
    if "name" in fields_set and name is not None:
        folder.name = name
    if "parent_folder_id" in fields_set:
        new_parent = parent_folder_id
        if new_parent == folder.id:
            raise ConflictError("a folder cannot be moved into itself")
        if new_parent is not None:
            target = await get_folder_checked(
                session, ctx, new_parent, workspace_id=folder.workspace_id
            )
            if await _is_ancestor_or_self(session, candidate_id=folder.id, folder_id=target.id):
                raise ConflictError("cannot move a folder into one of its own subfolders")
        folder.parent_folder_id = new_parent
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="folder.updated", target_type="folder", target_id=str(folder.id))
    await session.commit()
    return folder


async def _collect_subtree_folder_ids(
    session: AsyncSession, folder_id: UUID, *, workspace_id: UUID
) -> list[UUID]:
    """Breadth-first walk collecting folder_id and every descendant folder id,
    all depths. Postgres-only reads, no external I/O -- safe to run inline in
    a request handler.

    `workspace_id` (the ALREADY-VALIDATED root folder's own workspace_id, per
    get_folder_checked) is asserted on every hop of the walk, not just relied
    on structurally: today a validated folder's subtree is workspace-homogeneous
    BY CONSTRUCTION (every folder-write path -- create_folder, ensure_path,
    rename_or_move_folder -- pins a folder's parent to the same workspace), so
    this predicate is a no-op for every legitimate call. It exists as
    defense-in-depth (iron rule 1: queries re-assert the tenant/workspace
    boundary themselves, never rely solely on invariants holding elsewhere)
    for the single most destructive operation this module has -- a cascade
    delete walking an entire subtree."""
    ids = [folder_id]
    frontier = [folder_id]
    for _ in range(_MAX_TREE_DEPTH):
        if not frontier:
            break
        rows = list(
            (
                await session.execute(
                    select(Folder.id).where(
                        Folder.parent_folder_id.in_(frontier),
                        Folder.workspace_id == workspace_id,
                    )
                )
            ).scalars()
        )
        if not rows:
            break
        ids.extend(rows)
        frontier = rows
    return ids


async def count_subtree(
    session: AsyncSession, ctx: TenantContext, folder_id: UUID
) -> tuple[int, int]:
    """Read-only preview for the delete confirmation dialog: how many
    documents and subfolders a delete_folder(folder_id) call would cascade
    over, computed BEFORE that (irreversible) delete actually runs --
    delete_folder itself only returns the document count, and only AFTER
    deletion already happened, so it can't back this UI. Reuses the exact
    same _collect_subtree_folder_ids walk delete_folder uses, so the preview
    and the real delete can never disagree on which folders are "the
    subtree." Returns (document_count, subfolder_count); subfolder_count
    excludes folder_id itself, matching the frontend's existing
    countSubtree-minus-1 client-side convention."""
    folder = await get_folder_checked(session, ctx, folder_id)
    workspace_id = folder.workspace_id  # captured once; see _collect_subtree_folder_ids docstring
    folder_ids = await _collect_subtree_folder_ids(session, folder.id, workspace_id=workspace_id)
    document_count = (
        await session.execute(
            select(func.count()).select_from(Document).where(
                Document.folder_id.in_(folder_ids),
                Document.workspace_id == workspace_id,  # defense-in-depth, see above
            )
        )
    ).scalar_one()
    return document_count, len(folder_ids) - 1


async def delete_folder(session: AsyncSession, ctx: TenantContext, folder_id: UUID) -> list[UUID]:
    """App-level cascade (never a raw DB cascade on Document -- see the
    Folder docstring): every document anywhere in this folder's subtree goes
    through the EXACT SAME delete path DELETE /documents/{id} already uses
    (flip status='deleting', enqueue_delete -- Qdrant points, MinIO blob,
    Postgres row, audit entry, all async via Celery) -- INCLUDING
    get_document_checked's per-document ACL rule (RBAC-01 fix), preflighted
    against the whole subtree atomically before any row is mutated: if even
    one collected document fails the check, the entire delete is refused and
    nothing changes. Only once every document's status has flipped are the
    Folder rows themselves removed:
    Document.folder_id is ondelete=SET NULL, so a document still
    mid-async-deletion never blocks the folder row's removal -- it just loses
    its (soon irrelevant) folder reference. Folder->Folder cascade
    (ondelete=CASCADE) removes every subfolder row in one DB statement once
    the top folder is deleted, since folders carry no external state of their
    own.

    This function deliberately never calls enqueue_delete itself (mirrors
    documents/service.py's promote_lineage/set_approved inversion, Plan K
    Task 11): modules/ must never import worker/, so rather than importing
    worker.tasks here (even just at call time), this function returns the
    list of document ids that need enqueue_delete called on them. The ONE
    route call site (api/routes/documents.py, which already imports
    worker.tasks freely for the single-document delete route) performs the
    actual enqueue in a loop."""
    folder = await get_folder_checked(session, ctx, folder_id)
    workspace_id = folder.workspace_id  # captured once; see _collect_subtree_folder_ids docstring
    folder_ids = await _collect_subtree_folder_ids(session, folder.id, workspace_id=workspace_id)
    docs = list(
        (
            await session.execute(
                select(Document).where(
                    Document.folder_id.in_(folder_ids),
                    Document.workspace_id == workspace_id,  # defense-in-depth, see above
                )
            )
        ).scalars()
    )
    # RBAC-01 (release blocker): the cascade must not delete a document the
    # caller could not delete individually via DELETE /documents/{id} (which
    # calls get_document_checked). Preflighted atomically, BEFORE any
    # mutation below -- a local import avoids a real circular import
    # (documents/service.py already imports this module at module scope for
    # create_from_upload's folder_id validation).
    from ragz.modules.documents.service import user_can_access_document

    if any(not user_can_access_document(ctx, doc) for doc in docs):
        # Non-enumerating: a member must not learn a restricted doc exists
        # here, same error as get_folder_checked's own workspace gate.
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    # ONE transaction for the whole cascade (Cubic P1). This used to be two
    # commits here plus a third in the route when it published the delete
    # events, so a crash in between could leave every document in the subtree
    # at "deleting" with no work queued for any of them -- the folder gone, the
    # documents stranded. Publishing here, alongside the status flips, makes the
    # cascade genuinely all-or-nothing; the route only nudges the dispatcher.
    for doc in docs:
        doc.status = "deleting"
        outbox_service.publish(
            session,
            topic="documents.delete",
            payload={"document_id": str(doc.id), "actor_id": str(ctx.user_id)},
            queue="interactive",
        )
    document_ids = [doc.id for doc in docs]
    await session.delete(folder)  # subtree cascades via Folder.parent_folder_id's DB FK
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id, action="folder.deleted",
        target_type="folder", target_id=str(folder.id),
    )
    await session.commit()
    return document_ids
