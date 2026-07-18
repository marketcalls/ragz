from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.app_settings import get_app_setting, set_app_setting
from raghub.core.config import Settings, get_settings
from raghub.core.errors import NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.oidc import OIDC_CLIENT_ID_KEY, OIDC_ISSUER_KEY, OIDC_SECRET_NAME
from raghub.modules.secrets import service as secrets_service
from raghub.modules.tenancy.context import TenantContext, require_role
from raghub.modules.tenancy.models import Organization

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


async def _secret_set(session: AsyncSession) -> bool:
    return any(s.name == OIDC_SECRET_NAME for s in await secrets_service.list_secrets(session))


@router.get("/sso", response_model=SsoConfigOut)
async def get_sso(session: SessionDep, ctx: SuperDep) -> SsoConfigOut:
    return SsoConfigOut(
        issuer=await get_app_setting(session, OIDC_ISSUER_KEY),
        client_id=await get_app_setting(session, OIDC_CLIENT_ID_KEY),
        client_secret_set=await _secret_set(session),
    )


@router.put("/sso", response_model=SsoConfigOut)
async def put_sso(
    body: SsoConfigIn, session: SessionDep, settings: SettingsDep, ctx: SuperDep
) -> SsoConfigOut:
    await set_app_setting(session, OIDC_ISSUER_KEY, body.issuer)
    await set_app_setting(session, OIDC_CLIENT_ID_KEY, body.client_id)
    if body.client_secret:
        await secrets_service.set_secret(
            session, actor_id=ctx.user_id, name=OIDC_SECRET_NAME,
            value=body.client_secret, settings=settings,
        )
    await record_audit(session, org_id=None, actor_id=ctx.user_id,
                       action="sso.config_changed", target_type="sso", target_id="oidc")
    await session.commit()
    return SsoConfigOut(issuer=body.issuer, client_id=body.client_id,
                        client_secret_set=await _secret_set(session))


@router.get("/orgs", response_model=list[OrgOut])
async def list_orgs(session: SessionDep, ctx: SuperDep) -> list[OrgOut]:
    orgs = (await session.execute(select(Organization).order_by(Organization.name))).scalars()
    return [OrgOut.model_validate(o) for o in orgs]


@router.put("/orgs/{org_id}/sso-domains", response_model=OrgOut)
async def put_sso_domains(
    org_id: UUID, body: SsoDomainsIn, session: SessionDep, ctx: SuperDep
) -> OrgOut:
    org = (
        await session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("organization not found")
    org.sso_domains = sorted({d.strip().lower() for d in body.domains if d.strip()}) or None
    await record_audit(session, org_id=org.id, actor_id=ctx.user_id,
                       action="org.sso_domains_changed", target_type="organization",
                       target_id=str(org.id))
    await session.commit()
    return OrgOut.model_validate(org)
