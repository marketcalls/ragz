from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.auth import service
from raghub.modules.auth.schemas import UserOut, UserPatch
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext, require_role
from raghub.modules.tenancy.schemas import CustomRoleAssign

router = APIRouter(prefix="/users", tags=["users"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, ctx: AdminDep) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in await service.list_users(session, ctx)]


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID, body: UserPatch, session: SessionDep, ctx: AdminDep
) -> UserOut:
    user = None
    if body.active is not None:
        user = await service.set_user_active(session, ctx, user_id, body.active)
    if body.role is not None:
        user = await service.set_user_role(session, ctx, user_id, body.role)
    if user is None:
        user = await service.get_org_user(session, ctx, user_id)
    return UserOut.model_validate(user)


@router.put("/{user_id}/custom-role", status_code=204)
async def assign_custom_role(
    user_id: UUID, body: CustomRoleAssign, session: SessionDep, ctx: AdminDep
) -> None:
    await tenancy_service.assign_custom_role(session, ctx, user_id, body.role_template_id)
