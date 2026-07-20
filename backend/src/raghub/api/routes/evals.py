from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.evals import service
from raghub.modules.evals.schemas import EvalRunOut, GoldenQueryCreate, GoldenQueryOut
from raghub.modules.tenancy.context import TenantContext, require_permission
from raghub.worker.tasks import enqueue_eval_run

router = APIRouter(tags=["evals"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ConfigureDep = Annotated[TenantContext, Depends(require_permission("workspace.configure"))]


@router.get("/workspaces/{workspace_id}/golden-queries", response_model=list[GoldenQueryOut])
async def list_golden_queries(
    workspace_id: UUID, session: SessionDep, ctx: ConfigureDep
) -> list[GoldenQueryOut]:
    return [
        GoldenQueryOut.model_validate(g)
        for g in await service.list_golden_queries(session, ctx, workspace_id)
    ]


@router.post(
    "/workspaces/{workspace_id}/golden-queries", status_code=201, response_model=GoldenQueryOut
)
async def create_golden_query(
    workspace_id: UUID, body: GoldenQueryCreate, session: SessionDep, ctx: ConfigureDep
) -> GoldenQueryOut:
    gq = await service.create_golden_query(
        session, ctx, workspace_id, question=body.question,
        expected_document_ids=body.expected_document_ids,
    )
    return GoldenQueryOut.model_validate(gq)


@router.delete("/golden-queries/{query_id}", status_code=204)
async def delete_golden_query(query_id: UUID, session: SessionDep, ctx: ConfigureDep) -> None:
    await service.delete_golden_query(session, ctx, query_id)


@router.post("/workspaces/{workspace_id}/evals/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_eval_run(workspace_id: UUID, ctx: ConfigureDep) -> None:
    enqueue_eval_run(workspace_id, "manual")


@router.get("/workspaces/{workspace_id}/evals/runs", response_model=list[EvalRunOut])
async def list_eval_runs(
    workspace_id: UUID, session: SessionDep, ctx: ConfigureDep
) -> list[EvalRunOut]:
    return [
        EvalRunOut.model_validate(r)
        for r in await service.list_eval_runs(session, ctx, workspace_id)
    ]
