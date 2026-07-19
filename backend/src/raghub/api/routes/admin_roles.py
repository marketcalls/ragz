from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.tenancy import service
from raghub.modules.tenancy.context import TenantContext, require_role
from raghub.modules.tenancy.schemas import RoleTemplateCreate, RoleTemplateOut, RoleTemplatePatch

router = APIRouter(prefix="/admin/roles", tags=["admin-roles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
# Org admins need the list to assign templates to their users; only
# superadmin may create/modify/delete the global template catalog.
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
SuperDep = Annotated[TenantContext, Depends(require_role("superadmin"))]


@router.get("", response_model=list[RoleTemplateOut])
async def list_role_templates(session: SessionDep, ctx: AdminDep) -> list[RoleTemplateOut]:
    return [
        RoleTemplateOut.model_validate(t) for t in await service.list_role_templates(session)
    ]


@router.post("", status_code=201, response_model=RoleTemplateOut)
async def create_role_template(
    body: RoleTemplateCreate, session: SessionDep, ctx: SuperDep
) -> RoleTemplateOut:
    template = await service.create_role_template(
        session, ctx, name=body.name, description=body.description, permissions=body.permissions
    )
    return RoleTemplateOut.model_validate(template)


@router.patch("/{role_template_id}", response_model=RoleTemplateOut)
async def patch_role_template(
    role_template_id: UUID, body: RoleTemplatePatch, session: SessionDep, ctx: SuperDep
) -> RoleTemplateOut:
    template = await service.update_role_template(
        session, ctx, role_template_id,
        name=body.name, description=body.description, permissions=body.permissions,
    )
    return RoleTemplateOut.model_validate(template)


@router.delete("/{role_template_id}", status_code=204)
async def delete_role_template(
    role_template_id: UUID, session: SessionDep, ctx: SuperDep
) -> None:
    await service.delete_role_template(session, ctx, role_template_id)
