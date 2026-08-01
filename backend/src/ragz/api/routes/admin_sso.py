from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.modules.auth import oidc as oidc_service
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/admin", tags=["admin-sso"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SuperDep = Annotated[TenantContext, Depends(require_role("superadmin"))]


class SsoConfigOut(BaseModel):
    issuer: str | None
    client_id: str | None
    client_secret_set: bool


class SsoConfigIn(BaseModel):
    issuer: str
    client_id: str
    client_secret: str | None = None  # write-only; omit to keep the stored one


class OrgOut(BaseModel):
    id: UUID
    name: str
    sso_domains: list[str] | None

    model_config = {"from_attributes": True}


class SsoDomainsIn(BaseModel):
    domains: list[str]


@router.get("/sso", response_model=SsoConfigOut)
async def get_sso(session: SessionDep, ctx: SuperDep) -> SsoConfigOut:
    config = await oidc_service.get_sso_config(session)
    return SsoConfigOut(
        issuer=config.issuer, client_id=config.client_id,
        client_secret_set=config.client_secret_set,
    )


@router.put("/sso", response_model=SsoConfigOut)
async def put_sso(
    body: SsoConfigIn, session: SessionDep, settings: SettingsDep, ctx: SuperDep
) -> SsoConfigOut:
    config = await oidc_service.set_sso_config(
        session, actor_id=ctx.user_id, issuer=body.issuer, client_id=body.client_id,
        client_secret=body.client_secret, settings=settings,
    )
    return SsoConfigOut(
        issuer=config.issuer, client_id=config.client_id,
        client_secret_set=config.client_secret_set,
    )


@router.get("/orgs", response_model=list[OrgOut])
async def list_orgs(session: SessionDep, ctx: SuperDep) -> list[OrgOut]:
    orgs = await tenancy_service.list_organizations(session)
    return [OrgOut.model_validate(o) for o in orgs]


@router.put("/orgs/{org_id}/sso-domains", response_model=OrgOut)
async def put_sso_domains(
    org_id: UUID, body: SsoDomainsIn, session: SessionDep, ctx: SuperDep
) -> OrgOut:
    org = await tenancy_service.set_org_sso_domains(
        session, actor_id=ctx.user_id, org_id=org_id, domains=body.domains
    )
    return OrgOut.model_validate(org)
