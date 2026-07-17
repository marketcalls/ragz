from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.tenancy import service
from raghub.modules.tenancy.context import TenantContext, get_tenant_context, require_role
from raghub.modules.tenancy.schemas import MemberAdd, WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.post("", status_code=201, response_model=WorkspaceOut)
async def create(body: WorkspaceCreate, session: SessionDep, ctx: AdminDep) -> WorkspaceOut:
    ws = await service.create_workspace(session, ctx, body.name)
    return WorkspaceOut.model_validate(ws)


@router.get("", response_model=list[WorkspaceOut])
async def list_(session: SessionDep, ctx: CtxDep) -> list[WorkspaceOut]:
    return [WorkspaceOut.model_validate(w) for w in await service.list_workspaces(session, ctx)]


@router.post("/{workspace_id}/members", status_code=204)
async def add_member(
    workspace_id: UUID, body: MemberAdd, session: SessionDep, ctx: AdminDep
) -> None:
    await service.add_member(session, ctx, workspace_id, body.user_id, body.role)
