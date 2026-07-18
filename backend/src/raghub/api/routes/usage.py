from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import get_settings
from raghub.modules.models import keys
from raghub.modules.quotas import service
from raghub.modules.quotas.schemas import OrgQuotaIn, OrgQuotaOut, UserQuotaIn
from raghub.modules.tenancy.context import TenantContext, require_role

router = APIRouter(tags=["usage"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
SuperDep = Annotated[TenantContext, Depends(require_role("superadmin"))]


@router.get("/admin/orgs/{org_id}/quota", response_model=OrgQuotaOut | None)
async def get_org_quota(
    org_id: UUID, session: SessionDep, ctx: SuperDep
) -> OrgQuotaOut | None:
    row = await service.get_org_quota(session, org_id)
    return None if row is None else OrgQuotaOut.model_validate(row)


@router.put("/admin/orgs/{org_id}/quota", response_model=OrgQuotaOut)
async def put_org_quota(
    org_id: UUID, body: OrgQuotaIn, session: SessionDep, ctx: SuperDep
) -> OrgQuotaOut:
    row = await service.set_org_quota(
        session, ctx, org_id, monthly_tokens=body.monthly_tokens,
        default_user_monthly_tokens=body.default_user_monthly_tokens,
        reset_day=body.reset_day,
    )
    return OrgQuotaOut.model_validate(row)


@router.put("/users/{user_id}/quota", status_code=204)
async def put_user_quota(
    user_id: UUID, body: UserQuotaIn, session: SessionDep, ctx: AdminDep
) -> None:
    await service.set_user_quota(session, ctx, user_id, body.monthly_tokens)
    await keys.update_user_budget(
        session, get_settings(), user_id=user_id, monthly_tokens=body.monthly_tokens,
    )
