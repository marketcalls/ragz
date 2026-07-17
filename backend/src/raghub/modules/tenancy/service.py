from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError
from raghub.modules.auth.models import User
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace, WorkspaceMember


async def create_workspace(session: AsyncSession, ctx: TenantContext, name: str) -> Workspace:
    ws = Workspace(org_id=ctx.org_id, name=name)
    session.add(ws)
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
    await session.commit()
