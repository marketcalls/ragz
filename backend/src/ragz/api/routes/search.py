from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.documents import metadata
from ragz.modules.retrieval.schemas import ChunkOut, SearchRequest, SearchResponse
from ragz.modules.retrieval.service import retrieve
from ragz.modules.tenancy.context import TenantContext, get_tenant_context, require_action

router = APIRouter(tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
# Task 5 (RBAC-03): direct search had no permission gate at all -- any
# authenticated member could search regardless of role-template contents.
SearchDep = Annotated[TenantContext, Depends(require_action("search.execute"))]


@router.post("/workspaces/{workspace_id}/search", response_model=SearchResponse)
async def search_workspace(
    workspace_id: UUID, body: SearchRequest, session: SessionDep, ctx: SearchDep
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
