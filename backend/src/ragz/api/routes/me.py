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
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.auth import service as auth_service
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext, get_tenant_context

router = APIRouter(tags=["me"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]


class AuthorizationOut(BaseModel):
    role: str
    permissions: list[str]
    policy_version: int | None


@router.get("/me/authorization", response_model=AuthorizationOut)
async def get_my_authorization(session: SessionDep, ctx: CtxDep) -> AuthorizationOut:
    # Two module accessors rather than two ORM queries in the route: entrypoints
    # call public services (Phase 2 item 1). auth owns who has which custom
    # role; tenancy owns what version that role template is on.
    policy_version: int | None = None
    custom_role_id = await auth_service.get_custom_role_id(session, ctx.user_id)
    if custom_role_id is not None:
        policy_version = await tenancy_service.get_role_template_version(
            session, custom_role_id
        )
    return AuthorizationOut(
        role=ctx.role, permissions=sorted(ctx.permissions), policy_version=policy_version
    )
