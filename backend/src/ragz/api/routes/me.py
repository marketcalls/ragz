"""RBAC-12: the ONE endpoint the frontend uses to render permission-aware
nav/actions instead of trusting the fixed JWT role claim (frontend/src/lib/
jwt.ts). Backend enforcement (require_action on every route) remains the
actual security boundary regardless of what this reports -- this route only
describes the caller's OWN already-computed TenantContext, so it carries no
separate authorization decision beyond "is authenticated" (see policy.py's
PUBLIC_ROUTES entry)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.auth.models import User
from ragz.modules.tenancy.context import TenantContext, get_tenant_context
from ragz.modules.tenancy.models import RoleTemplate

router = APIRouter(tags=["me"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]


class AuthorizationOut(BaseModel):
    role: str
    permissions: list[str]
    policy_version: int | None


@router.get("/me/authorization", response_model=AuthorizationOut)
async def get_my_authorization(session: SessionDep, ctx: CtxDep) -> AuthorizationOut:
    policy_version: int | None = None
    user = (
        await session.execute(select(User).where(User.id == ctx.user_id))
    ).scalar_one_or_none()
    if user is not None and user.custom_role_id is not None:
        template = await session.get(RoleTemplate, user.custom_role_id)
        if template is not None:
            policy_version = template.version
    return AuthorizationOut(
        role=ctx.role, permissions=sorted(ctx.permissions), policy_version=policy_version
    )
