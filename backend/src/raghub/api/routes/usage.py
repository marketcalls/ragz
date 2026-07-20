from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.modules.chat import service as chat_service
from raghub.modules.evals import service as evals_service
from raghub.modules.models import keys
from raghub.modules.quotas import service
from raghub.modules.quotas.schemas import (
    OrgQuotaIn,
    OrgQuotaOut,
    OrgUsage,
    UsageMeterOut,
    UsageSummaryOut,
    UserQuotaIn,
    UserQuotaOut,
)
from raghub.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_permission,
    require_role,
)

router = APIRouter(tags=["usage"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
SuperDep = Annotated[TenantContext, Depends(require_role("superadmin"))]
# Task 13 (RBAC-2): org-scoped analytics dashboard narrows/widens independently
# of admin role; GET .../orgs (platform-wide, superadmin) is unchanged.
AnalyticsDep = Annotated[TenantContext, Depends(require_permission("analytics.view"))]


@router.get("/admin/orgs/{org_id}/quota", response_model=OrgQuotaOut | None)
async def get_org_quota(
    org_id: UUID, session: SessionDep, ctx: SuperDep
) -> OrgQuotaOut | None:
    row = await service.get_org_quota(session, org_id)
    return None if row is None else OrgQuotaOut.model_validate(row)


@router.put("/admin/orgs/{org_id}/quota", response_model=OrgQuotaOut)
async def put_org_quota(
    org_id: UUID, body: OrgQuotaIn, request: Request, session: SessionDep,
    ctx: SuperDep, settings: SettingsDep,
) -> OrgQuotaOut:
    row = await service.set_org_quota(
        session, ctx, org_id, monthly_tokens=body.monthly_tokens,
        default_user_monthly_tokens=body.default_user_monthly_tokens,
        reset_day=body.reset_day,
    )
    # Live-user report: existing per-user gateway budgets were minted at
    # allocation time and go stale on an org-quota change, hard-blocking a
    # user whose allocation was just raised. Best-effort re-mirror (each
    # update_user_budget call already swallows gateway errors) for every org
    # member who actually holds a vkey.
    allocations = await service.org_member_effective_allocations(session, org_id)
    vkey_users = await keys.filter_users_with_vkey(session, allocations.keys())
    transport = request.app.state.litellm_transport
    for user_id in vkey_users:
        await keys.update_user_budget(
            session, settings, user_id=user_id, monthly_tokens=allocations[user_id],
            transport=transport,
        )
    return OrgQuotaOut.model_validate(row)


@router.get("/users/{user_id}/quota", response_model=UserQuotaOut)
async def get_user_quota(
    user_id: UUID, request: Request, session: SessionDep, ctx: AdminDep,
) -> UserQuotaOut:
    return await service.get_user_quota_with_usage(
        session, request.app.state.redis, ctx, user_id
    )


@router.put("/users/{user_id}/quota", status_code=204)
async def put_user_quota(
    user_id: UUID, body: UserQuotaIn, request: Request, session: SessionDep,
    ctx: AdminDep, settings: SettingsDep,
) -> None:
    await service.set_user_quota(session, ctx, user_id, body.monthly_tokens)
    await keys.update_user_budget(
        session, settings, user_id=user_id, monthly_tokens=body.monthly_tokens,
        transport=request.app.state.litellm_transport,
    )


@router.get("/usage/me", response_model=UsageMeterOut)
async def usage_me(request: Request, session: SessionDep, ctx: CtxDep) -> UsageMeterOut:
    status = await service.get_usage_status(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    return UsageMeterOut(
        used_tokens=status.used_tokens, allocated_tokens=status.allocated_tokens,
        resets_at=status.resets_at, warning=status.warning,
    )


# Task 4 (Plan J, SAFE-2): route-local composed response, not a change to
# quotas/schemas.py — chat-owned Auditor data is composed at the API edge
# (J-C14's CatalogOut precedent), keeping module boundaries clean.
class AnswerQualityOut(BaseModel):
    audited_count: int
    avg_grounding_score: float | None
    avg_completeness_score: float | None
    low_score_count: int


class WorstAnswerOut(BaseModel):
    message_id: UUID
    chat_id: UUID
    content_snippet: str
    grounding_score: float | None
    completeness_score: float | None
    created_at: datetime


class EvalTrendOut(BaseModel):
    """Task 12 (Plan J, §6): the latest EvalRun per workspace, for the
    org-wide dashboard trend table. model_config enables model_validate
    straight off the evals_service.EvalRun ORM instance (which carries
    workspace_name as an extra attribute -- see
    latest_eval_run_per_workspace's docstring)."""

    model_config = {"from_attributes": True}

    workspace_id: UUID
    workspace_name: str
    hit_rate: float | None
    citation_precision: float | None
    avg_faithfulness: float | None
    created_at: datetime


class DashboardSummaryOut(UsageSummaryOut):
    answer_quality: AnswerQualityOut
    worst_answers: list[WorstAnswerOut]
    eval_trend: list[EvalTrendOut]


@router.get("/admin/usage/summary", response_model=DashboardSummaryOut)
async def usage_summary(
    session: SessionDep, ctx: AnalyticsDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> DashboardSummaryOut:
    base = await service.org_usage_summary(session, org_id=ctx.org_id, days=days)
    quality = await chat_service.answer_quality_summary(session, ctx, days=days)
    trend = await evals_service.latest_eval_run_per_workspace(session, ctx.org_id)
    return DashboardSummaryOut(
        **UsageSummaryOut.model_validate(base).model_dump(),
        answer_quality=AnswerQualityOut(
            audited_count=quality.audited_count,
            avg_grounding_score=quality.avg_grounding_score,
            avg_completeness_score=quality.avg_completeness_score,
            low_score_count=quality.low_score_count,
        ),
        worst_answers=[
            WorstAnswerOut(
                message_id=w.message_id, chat_id=w.chat_id,
                content_snippet=w.content_snippet, grounding_score=w.grounding_score,
                completeness_score=w.completeness_score, created_at=w.created_at,
            )
            for w in quality.worst
        ],
        eval_trend=[EvalTrendOut.model_validate(t) for t in trend],
    )


@router.get("/admin/usage/orgs", response_model=list[OrgUsage])
async def usage_by_org(session: SessionDep, ctx: SuperDep) -> list[OrgUsage]:
    return [
        OrgUsage.model_validate(row)
        for row in await service.platform_usage_by_org(session)
    ]
