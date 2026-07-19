from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.modules.secrets import service
from raghub.modules.secrets.schemas import SecretOut, SecretWrite
from raghub.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/admin/secrets", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# require_role() with NO roles: only the superadmin bypass passes -> superadmin-only guard.
SuperadminDep = Annotated[TenantContext, Depends(require_role())]
# model:{uuid} registry secret names contain ':' -- the pattern allows it.
SecretName = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]


@router.put("/{name:path}", response_model=SecretOut)
async def put_secret(
    name: SecretName, body: SecretWrite, session: SessionDep, settings: SettingsDep,
    ctx: SuperadminDep,
) -> SecretOut:
    row = await service.set_secret(
        session, actor_id=ctx.user_id, name=name, value=body.value, settings=settings
    )
    return SecretOut.model_validate(row)


@router.get("", response_model=list[SecretOut])
async def list_secrets(session: SessionDep, ctx: SuperadminDep) -> list[SecretOut]:
    return [SecretOut.model_validate(s) for s in await service.list_secrets(session)]


@router.delete("/{name:path}", status_code=204)
async def delete_secret(
    name: SecretName, session: SessionDep, ctx: SuperadminDep
) -> None:
    await service.delete_secret(session, actor_id=ctx.user_id, name=name)
