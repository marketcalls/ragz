"""Folder tree service: create/list/rename/move (this task). Cascade delete
lives in Task 3's delete_folder, added to this same file, since it shares
the subtree-walk helper below."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Folder
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.service import get_workspace_checked

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
