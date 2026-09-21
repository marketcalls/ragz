from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.api.routes import chats
from ragz.core.config import Settings, get_settings
from ragz.core.errors import ConflictError
from ragz.modules.chat.llm import LiteLLMStreamer, LLMCompleter
from ragz.modules.evals import comparison, service
from ragz.modules.evals.schemas import (
    AnswerComparisonOut,
    AnswerComparisonRequest,
    EvalRunOut,
    GoldenQueryCreate,
    GoldenQueryOut,
)
from ragz.modules.models import service as models_service
from ragz.modules.outbox import service as outbox_service
from ragz.modules.quotas import service as quota_service
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext, rate_limit_user, require_action
from ragz.modules.tenancy.views import WorkspaceView
from ragz.worker.outbox import nudge

router = APIRouter(tags=["evals"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# sec RAGZ-PUB-01: every eval route now ENFORCES exactly the action it DECLARES
# in api/policy.py (previously all five shared workspace.configure, so a role
# granted only evals.read could still trigger a run, and vice versa). read =
# view golden queries / run history; manage = author/delete golden queries;
# run = trigger an eval run.
EvalsReadDep = Annotated[TenantContext, Depends(require_action("evals.read"))]
EvalsManageDep = Annotated[TenantContext, Depends(require_action("evals.manage"))]
EvalsRunDep = Annotated[TenantContext, Depends(require_action("evals.run"))]
CompareCtxDep = Annotated[TenantContext, Depends(rate_limit_user("eval_compare", 10, 60))]


@router.get("/workspaces/{workspace_id}/golden-queries", response_model=list[GoldenQueryOut])
async def list_golden_queries(
    workspace_id: UUID, session: SessionDep, ctx: EvalsReadDep
) -> list[GoldenQueryOut]:
    return [
        GoldenQueryOut.model_validate(g)
        for g in await service.list_golden_queries(session, ctx, workspace_id)
    ]


@router.post(
    "/workspaces/{workspace_id}/golden-queries", status_code=201, response_model=GoldenQueryOut
)
async def create_golden_query(
    workspace_id: UUID, body: GoldenQueryCreate, session: SessionDep, ctx: EvalsManageDep
) -> GoldenQueryOut:
    gq = await service.create_golden_query(
        session, ctx, workspace_id, question=body.question,
        expected_document_ids=body.expected_document_ids,
    )
    return GoldenQueryOut.model_validate(gq)


@router.delete("/golden-queries/{query_id}", status_code=204)
async def delete_golden_query(query_id: UUID, session: SessionDep, ctx: EvalsManageDep) -> None:
    await service.delete_golden_query(session, ctx, query_id)


@router.post("/workspaces/{workspace_id}/evals/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_eval_run(workspace_id: UUID, session: SessionDep, ctx: EvalsRunDep) -> None:
    # Task 11 review fix: workspace.configure alone doesn't prove workspace_id
    # belongs to ctx.org_id -- resolve it the same way every other route in
    # this file does before enqueuing (see evals/service.py's
    # check_workspace_for_trigger), otherwise a caller can burn another org's
    # LLM/quota budget by guessing a UUID.
    await service.check_workspace_for_trigger(session, ctx, workspace_id)
    outbox_service.publish(
        session,
        topic="evals.run",
        payload={"workspace_id": str(workspace_id), "triggered_by": "manual"},
    )
    await session.commit()
    await nudge()


@router.post(
    "/workspaces/{workspace_id}/evals/compare",
    response_model=AnswerComparisonOut,
    dependencies=[Depends(require_action("evals.run"))],
)
async def compare_answers(
    workspace_id: UUID,
    body: AnswerComparisonRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    ctx: CompareCtxDep,
) -> AnswerComparisonOut:
    """Transient A/B answer generation; does not create chats or PATCH settings."""
    workspace = await tenancy_service.get_workspace(session, ctx, workspace_id)
    model = await models_service.resolve_model(
        session,
        requested_model_id=body.model_id,
        default_model_id=workspace.default_model_id,
    )
    await quota_service.check_quota(
        session,
        request.app.state.redis,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
    )
    completer: LLMCompleter | None = request.app.state.llm_completer
    if completer is None:
        streamer = await chats._streamer(request, session, settings, ctx)
        if isinstance(streamer, LiteLLMStreamer):
            completer = streamer
    if completer is None:
        raise ConflictError("answer comparison requires a non-streaming LLM client")
    variants = await comparison.compare_answers(
        session,
        ctx,
        WorkspaceView.of(workspace),
        question=body.question,
        model_id=model.id,
        model_name=model.litellm_model_name,
        completer=completer,
        retriever=request.app.state.retriever,
        token_budget=settings.chat_context_token_budget,
    )
    return AnswerComparisonOut(variants=variants)


@router.get("/workspaces/{workspace_id}/evals/runs", response_model=list[EvalRunOut])
async def list_eval_runs(
    workspace_id: UUID, session: SessionDep, ctx: EvalsReadDep
) -> list[EvalRunOut]:
    return [
        EvalRunOut.model_validate(r)
        for r in await service.list_eval_runs(session, ctx, workspace_id)
    ]
