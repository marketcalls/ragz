from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.documents import metadata
from raghub.modules.retrieval.schemas import ChunkOut, SearchRequest, SearchResponse
from raghub.modules.retrieval.service import retrieve
from raghub.modules.tenancy.context import TenantContext, get_tenant_context

router = APIRouter(tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post("/workspaces/{workspace_id}/search", response_model=SearchResponse)
async def search_workspace(
    workspace_id: UUID, body: SearchRequest, session: SessionDep, ctx: CtxDep
) -> SearchResponse:
    clauses = (
        await metadata.build_clauses(session, ctx, workspace_id, body.metadata)
        if body.metadata
        else None
    )
    result = await retrieve(
        session, ctx, workspace_id, body.query, top_k=body.top_k, metadata_clauses=clauses
    )
    return SearchResponse(
        no_answer=result.no_answer,
        chunks=[
            ChunkOut(document_id=c.document_id, page=c.page, chunk_index=c.chunk_index,
                     text=c.text, score=c.score, section=c.section, version=c.version)
            for c in result.chunks
        ],
    )
