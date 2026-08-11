from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.tenancy import service
from ragz.modules.tenancy.context import TenantContext, require_role
from ragz.modules.tenancy.schemas import RoleTemplateCreate, RoleTemplateOut, RoleTemplatePatch

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


class ImpactOut(BaseModel):
    affected_users: int


@router.post("/{role_template_id}/activate", response_model=RoleTemplateOut)
async def activate_role_template_route(
    role_template_id: UUID, session: SessionDep, ctx: SuperDep
) -> RoleTemplateOut:
    template = await service.activate_role_template(session, ctx, role_template_id)
    return RoleTemplateOut.model_validate(template)


@router.get("/{role_template_id}/impact", response_model=ImpactOut)
async def role_template_impact_route(
    role_template_id: UUID, session: SessionDep, ctx: AdminDep
) -> ImpactOut:
    return ImpactOut(affected_users=await service.role_template_impact(session, role_template_id))


@router.post("/{role_template_id}/rollback", response_model=RoleTemplateOut)
async def rollback_role_template_route(
    role_template_id: UUID, session: SessionDep, ctx: SuperDep
) -> RoleTemplateOut:
    template = await service.rollback_role_template(session, ctx, role_template_id)
    return RoleTemplateOut.model_validate(template)
