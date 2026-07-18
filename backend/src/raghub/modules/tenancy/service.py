from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import User
from raghub.modules.models import service as models_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Group, UserGroup, Workspace, WorkspaceMember


async def create_workspace(session: AsyncSession, ctx: TenantContext, name: str) -> Workspace:
    ws = Workspace(org_id=ctx.org_id, name=name)
    session.add(ws)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.created", target_type="workspace",
                       target_id=str(ws.id))
    await session.commit()
    return ws


async def list_workspaces(session: AsyncSession, ctx: TenantContext) -> list[Workspace]:
    stmt = select(Workspace).where(Workspace.org_id == ctx.org_id)
    if ctx.role == "user":
        stmt = stmt.where(Workspace.id.in_(ctx.workspace_ids))
    return list((await session.execute(stmt.order_by(Workspace.name))).scalars())


async def add_member(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, user_id: UUID, role: str
) -> None:
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise NotFoundError("workspace not found")
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.member_added", target_type="workspace_member",
                       target_id=f"{workspace_id}:{user_id}")
    await session.commit()


async def get_workspace_checked(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> Workspace:
    """The one workspace-access gate used by documents and retrieval (iron rule 1's
    Postgres-side counterpart). Same 403 for cross-org and non-member so existence
    never leaks."""
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    if ctx.role == "user" and workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return ws


async def get_workspace(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> Workspace:
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None or (ctx.role == "user" and workspace_id not in ctx.workspace_ids):
        raise NotFoundError("workspace not found")
    return ws


async def set_default_model(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, model_id: UUID | None,
    *, commit: bool = True
) -> Workspace:
    ws = await get_workspace(session, ctx, workspace_id)
    if model_id is not None:
        await models_service.get_model(session, model_id)  # NotFoundError if unknown
    ws.default_model_id = model_id
    if commit:
        await session.commit()
    else:
        await session.flush()
    return ws


_RETRIEVAL_SETTINGS_FIELDS = {"top_k", "min_score", "rerank_enabled", "system_prompt_override"}


async def update_retrieval_settings(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID,
    updates: Mapping[str, object], *, commit: bool = True
) -> Workspace:
    """ADM-3 tuning knobs. `system_prompt_override` is the only nullable field —
    explicit null clears it; null for any other field is a 409."""
    ws = await get_workspace(session, ctx, workspace_id)
    for field, value in updates.items():
        if field not in _RETRIEVAL_SETTINGS_FIELDS:
            raise ConflictError(f"not a retrieval setting: {field}")
        if value is None and field != "system_prompt_override":
            raise ConflictError(f"{field} cannot be null")
        setattr(ws, field, value)
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.retrieval_settings_changed",
                       target_type="workspace", target_id=str(ws.id))
    if commit:
        await session.commit()
    else:
        await session.flush()
    return ws


async def create_group(session: AsyncSession, ctx: TenantContext, name: str) -> Group:
    group = Group(org_id=ctx.org_id, name=name)
    session.add(group)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.created", target_type="group", target_id=str(group.id))
    await session.commit()
    return group


async def _org_group(session: AsyncSession, ctx: TenantContext, group_id: UUID) -> Group:
    group = (
        await session.execute(
            select(Group).where(Group.id == group_id, Group.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if group is None:
        raise NotFoundError("group not found")
    return group


async def list_groups(
    session: AsyncSession, ctx: TenantContext
) -> list[tuple[Group, list[UUID]]]:
    groups = list(
        (
            await session.execute(
                select(Group).where(Group.org_id == ctx.org_id).order_by(Group.name)
            )
        ).scalars()
    )
    rows = (
        await session.execute(
            select(UserGroup.group_id, UserGroup.user_id)
            .join(Group, Group.id == UserGroup.group_id)
            .where(Group.org_id == ctx.org_id)
        )
    ).all()
    members: dict[UUID, list[UUID]] = {g.id: [] for g in groups}
    for group_id, user_id in rows:
        members[group_id].append(user_id)
    return [(g, sorted(members[g.id])) for g in groups]


async def delete_group(session: AsyncSession, ctx: TenantContext, group_id: UUID) -> None:
    group = await _org_group(session, ctx, group_id)
    await session.delete(group)  # user_groups rows cascade at the DB layer
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.deleted", target_type="group", target_id=str(group_id))
    await session.commit()


async def add_group_member(
    session: AsyncSession, ctx: TenantContext, group_id: UUID, user_id: UUID
) -> None:
    await _org_group(session, ctx, group_id)
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    session.add(UserGroup(group_id=group_id, user_id=user_id))
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.member_added", target_type="group",
                       target_id=f"{group_id}:{user_id}")
    await session.commit()


async def remove_group_member(
    session: AsyncSession, ctx: TenantContext, group_id: UUID, user_id: UUID
) -> None:
    await _org_group(session, ctx, group_id)
    await session.execute(
        sa_delete(UserGroup).where(
            UserGroup.group_id == group_id, UserGroup.user_id == user_id
        )
    )
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.member_removed", target_type="group",
                       target_id=f"{group_id}:{user_id}")
    await session.commit()
