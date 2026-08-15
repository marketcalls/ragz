from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.evals import service
from ragz.modules.evals.schemas import EvalRunOut, GoldenQueryCreate, GoldenQueryOut
from ragz.modules.tenancy.context import TenantContext, require_action
from ragz.worker.tasks import enqueue_eval_run

router = APIRouter(tags=["evals"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
# sec RAGZ-PUB-01: every eval route now ENFORCES exactly the action it DECLARES
# in api/policy.py (previously all five shared workspace.configure, so a role
# granted only evals.read could still trigger a run, and vice versa). read =
# view golden queries / run history; manage = author/delete golden queries;
# run = trigger an eval run.
EvalsReadDep = Annotated[TenantContext, Depends(require_action("evals.read"))]
EvalsManageDep = Annotated[TenantContext, Depends(require_action("evals.manage"))]
EvalsRunDep = Annotated[TenantContext, Depends(require_action("evals.run"))]


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
    enqueue_eval_run(workspace_id, "manual")


@router.get("/workspaces/{workspace_id}/evals/runs", response_model=list[EvalRunOut])
async def list_eval_runs(
    workspace_id: UUID, session: SessionDep, ctx: EvalsReadDep
) -> list[EvalRunOut]:
    return [
        EvalRunOut.model_validate(r)
        for r in await service.list_eval_runs(session, ctx, workspace_id)
    ]
