from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError, WorkspaceAccessDenied
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import User
from raghub.modules.models import service as models_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace, WorkspaceMember


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
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, model_id: UUID | None
) -> Workspace:
    ws = await get_workspace(session, ctx, workspace_id)
    if model_id is not None:
        await models_service.get_model(session, model_id)  # NotFoundError if unknown
    ws.default_model_id = model_id
    await session.commit()
    return ws
