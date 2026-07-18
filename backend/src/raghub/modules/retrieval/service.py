"""THE single Qdrant search code path (iron rule 1).

`_tenant_filter` below is the only function in the codebase allowed to construct a
Qdrant filter. `retrieve()` and `delete_document_points()` are its only callers.
The adversarial suite in tests/isolation/ exists to catch any regression here.
"""

import asyncio
from dataclasses import dataclass, replace
from uuid import UUID

import structlog
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from raghub.modules.retrieval.rerank import RerankUnavailable, get_reranker
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.service import get_workspace_checked


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: UUID
    page: int
    chunk_index: int
    text: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    no_answer: bool


def _tenant_filter(
    *, org_id: UUID, workspace_id: UUID | None = None, document_id: UUID | None = None
) -> models.Filter:
    """The ONE Qdrant filter builder. tenant_id is always a must-condition;
    acl_groups intersection lands here in Phase 2 without touching callers."""
    must: list[models.Condition] = [
        models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(org_id)))
    ]
    if workspace_id is not None:
        must.append(
            models.FieldCondition(
                key="workspace_id", match=models.MatchValue(value=str(workspace_id))
            )
        )
    if document_id is not None:
        must.append(
            models.FieldCondition(
                key="document_id", match=models.MatchValue(value=str(document_id))
            )
        )
    return models.Filter(must=must)


async def ensure_collection(embedding_model: str = "bge-m3") -> str:
    """Idempotent collection setup. Any model other than bge-m3 is rejected —
    the Phase 1 embedding-model lock (workspaces default to bge-m3)."""
    if embedding_model != "bge-m3":
        raise ValueError(f"unsupported embedding model: {embedding_model}")
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        await client.create_collection(
            COLLECTION,
            vectors_config={
                "dense": models.VectorParams(
                    size=get_settings().embedding_dim, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for field in ("tenant_id", "workspace_id", "document_id"):
            await client.create_payload_index(
                COLLECTION, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )
    return COLLECTION


_RERANK_PREFETCH = 50  # CHAT-2: rerank the top-50 fused candidates


def _chunk_from_point(point: models.ScoredPoint) -> RetrievedChunk:
    payload = point.payload or {}
    return RetrievedChunk(
        document_id=UUID(str(payload["document_id"])),
        page=int(payload["page"]),
        chunk_index=int(payload["chunk_index"]),
        text=str(payload["text"]),
        score=float(point.score),
    )


async def retrieve(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int | None = None,
) -> RetrievalResult:
    """Hybrid retrieval — the one code path (spec §3.3), Plan E additions:

    1. Workspace access gate (typed WorkspaceAccessDenied).
    2. top_k=None resolves to workspace.top_k (ADM-3).
    3. Qdrant prefetch dense + sparse under the tenant filter → RRF fusion
       (top-50 candidates when workspace.rerank_enabled, else top_k).
    4. rerank_enabled: cross-encoder scores the candidates; final top_k come
       back in reranker order carrying RERANKER scores, and no_answer compares
       the best reranker score against workspace.min_score. CALIBRATION: that
       threshold now reads in sigmoid cross-encoder space, not dense-cosine
       space — revisit min_score when flipping rerank_enabled.
    5. Reranker down → structlog warning and EXACTLY the pre-rerank behavior:
       fusion order, no_answer via best dense cosine (NFR graceful degradation).
    """
    ws = await get_workspace_checked(session, ctx, workspace_id)
    k = top_k if top_k is not None else ws.top_k
    await ensure_collection(ws.embedding_model)
    dense_vec = (await get_dense_embedder().embed([query]))[0]
    sparse_vec = (await asyncio.to_thread(embed_sparse, [query]))[0]
    flt = _tenant_filter(org_id=ctx.org_id, workspace_id=workspace_id)
    client = get_qdrant()
    fetch_k = _RERANK_PREFETCH if ws.rerank_enabled else k
    prefetch_limit = max(fetch_k, k * 4)
    fused = await client.query_points(
        COLLECTION,
        prefetch=[
            models.Prefetch(query=dense_vec, using="dense", filter=flt, limit=prefetch_limit),
            models.Prefetch(query=sparse_vec, using="sparse", filter=flt, limit=prefetch_limit),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=flt,  # belt and braces on top of the filtered prefetches
        limit=fetch_k,
        with_payload=True,
    )
    candidates = [_chunk_from_point(p) for p in fused.points]
    if not candidates:
        return RetrievalResult(chunks=[], no_answer=True)

    if ws.rerank_enabled:
        try:
            scores = await get_reranker().rerank(query, [c.text for c in candidates])
        except RerankUnavailable as exc:
            structlog.get_logger().warning(
                "reranker_unavailable_falling_back",
                workspace_id=str(workspace_id), error=str(exc),
            )
        else:
            order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
            top = order[:k]
            reranked = [replace(candidates[i], score=scores[i]) for i in top]
            return RetrievalResult(
                chunks=reranked, no_answer=scores[top[0]] < ws.min_score
            )

    chunks = candidates[:k]
    top_dense = await client.query_points(
        COLLECTION, query=dense_vec, using="dense", query_filter=flt,
        limit=1, with_payload=False,
    )
    best_cosine = float(top_dense.points[0].score) if top_dense.points else 0.0
    return RetrievalResult(chunks=chunks, no_answer=best_cosine < ws.min_score)


async def delete_document_points(org_id: UUID, document_id: UUID) -> None:
    """Deletion propagation entry point — lives here so filter knowledge never
    leaves this module. org_id scoping is defense in depth beyond the spec's
    document_id filter. A missing collection means nothing was ever indexed —
    deleting a never-indexed document must succeed (found by real-stack smoke)."""
    if not await get_qdrant().collection_exists(COLLECTION):
        return
    await get_qdrant().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=_tenant_filter(org_id=org_id, document_id=document_id)
        ),
        wait=True,
    )
