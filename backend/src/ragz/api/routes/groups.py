from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.tenancy import service
from ragz.modules.tenancy.context import TenantContext, require_role
from ragz.modules.tenancy.schemas import GroupCreate, GroupOut

router = APIRouter(prefix="/groups", tags=["groups"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.get("", response_model=list[GroupOut])
async def list_groups(session: SessionDep, ctx: AdminDep) -> list[GroupOut]:
    return [
        GroupOut(id=g.id, name=g.name, member_ids=members)
        for g, members in await service.list_groups(session, ctx)
    ]


@router.post("", status_code=201, response_model=GroupOut)
async def create_group(body: GroupCreate, session: SessionDep, ctx: AdminDep) -> GroupOut:
    group = await service.create_group(session, ctx, body.name)
    return GroupOut(id=group.id, name=group.name, member_ids=[])


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: UUID, session: SessionDep, ctx: AdminDep) -> None:
    await service.delete_group(session, ctx, group_id)


@router.put("/{group_id}/members/{user_id}", status_code=204)
async def add_member(
    group_id: UUID, user_id: UUID, session: SessionDep, ctx: AdminDep
) -> None:
    await service.add_group_member(session, ctx, group_id, user_id)


@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: UUID, user_id: UUID, session: SessionDep, ctx: AdminDep
) -> None:
    await service.remove_group_member(session, ctx, group_id, user_id)
