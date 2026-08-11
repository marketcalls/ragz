from uuid import UUID

from fastapi import APIRouter

from ragz.api.routes.models import SessionDep, SettingsDep, SuperadminDep
from ragz.modules.audit.service import record_audit
from ragz.modules.auth import api_keys_service as svc
from ragz.modules.auth.schemas import ApiKeyCreate, ApiKeyCreatedOut, ApiKeyOut

router = APIRouter(tags=["api-keys"])


@router.post("/admin/api-keys", status_code=201, response_model=ApiKeyCreatedOut)
async def create_api_key(
    body: ApiKeyCreate, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep
) -> ApiKeyCreatedOut:
    row, raw = await svc.generate_api_key(
        session, settings, actor_id=ctx.user_id, name=body.name,
        user_id=body.user_id, workspace_id=body.workspace_id, expires_at=body.expires_at,
    )
    await record_audit(
        session, org_id=row.org_id, actor_id=ctx.user_id,
        action="api_key.created", target_type="api_key", target_id=str(row.id),
    )
    await session.commit()
    return ApiKeyCreatedOut(
        **ApiKeyOut.model_validate(row, from_attributes=True).model_dump(), api_key=raw
    )


@router.get("/admin/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys_route(session: SessionDep, ctx: SuperadminDep) -> list[ApiKeyOut]:
    rows = await svc.list_api_keys(session)
    return [ApiKeyOut.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/admin/api-keys/{key_id}", status_code=204)
async def revoke_api_key_route(key_id: UUID, session: SessionDep, ctx: SuperadminDep) -> None:
    # RBAC-07: a missing key must 404 (not silently succeed), and the audit
    # event is attributed to the KEY's own org -- not the acting superadmin's.
    row = await svc.get_api_key(session, key_id=key_id)
    await svc.revoke_api_key(session, key_id=key_id)
    await record_audit(
        session, org_id=row.org_id, actor_id=ctx.user_id,
        action="api_key.revoked", target_type="api_key", target_id=str(key_id),
    )
    await session.commit()
