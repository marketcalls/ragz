"""THE single Qdrant search code path (iron rule 1).

`_tenant_filter` below is the only function in the codebase allowed to construct a
Qdrant filter. `retrieve()` and `delete_document_points()` are its only callers.
The adversarial suite in tests/isolation/ exists to catch any regression here.
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
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


async def retrieve(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int = 8,
) -> RetrievalResult:
    """Hybrid retrieval — the one code path (spec §3.3).

    1. Workspace access gate (typed WorkspaceAccessDenied).
    2. Embed query dense (backend per settings) + sparse (BM25).
    3. Qdrant prefetch dense + sparse under the tenant filter → RRF fusion.
    4. no_answer when the best DENSE COSINE is below workspace.min_score (RRF
       scores are rank-based/unitless, so the threshold is checked in cosine
       space via a dense top-1 query); nearest chunks are still returned.
    """
    ws = await get_workspace_checked(session, ctx, workspace_id)
    await ensure_collection(ws.embedding_model)
    dense_vec = (await get_dense_embedder().embed([query]))[0]
    sparse_vec = (await asyncio.to_thread(embed_sparse, [query]))[0]
    flt = _tenant_filter(org_id=ctx.org_id, workspace_id=workspace_id)
    client = get_qdrant()
    fused = await client.query_points(
        COLLECTION,
        prefetch=[
            models.Prefetch(query=dense_vec, using="dense", filter=flt, limit=top_k * 4),
            models.Prefetch(query=sparse_vec, using="sparse", filter=flt, limit=top_k * 4),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=flt,  # belt and braces on top of the filtered prefetches
        limit=top_k,
        with_payload=True,
    )
    chunks = []
    for p in fused.points:
        payload = p.payload or {}
        chunks.append(
            RetrievedChunk(
                document_id=UUID(str(payload["document_id"])),
                page=int(payload["page"]),
                chunk_index=int(payload["chunk_index"]),
                text=str(payload["text"]),
                score=float(p.score),
            )
        )
    if not chunks:
        return RetrievalResult(chunks=[], no_answer=True)
    top_dense = await client.query_points(
        COLLECTION, query=dense_vec, using="dense", query_filter=flt,
        limit=1, with_payload=False,
    )
    best_cosine = float(top_dense.points[0].score) if top_dense.points else 0.0
    return RetrievalResult(chunks=chunks, no_answer=best_cosine < ws.min_score)


async def delete_document_points(org_id: UUID, document_id: UUID) -> None:
    """Deletion propagation entry point — lives here so filter knowledge never
    leaves this module. org_id scoping is defense in depth beyond the spec's
    document_id filter."""
    await get_qdrant().delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=_tenant_filter(org_id=org_id, document_id=document_id)
        ),
        wait=True,
    )
