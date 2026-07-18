"""THE single Qdrant search code path (iron rule 1).

`_tenant_filter` below is the only function in the codebase allowed to construct
a Qdrant filter. Its only callers are `retrieve()`, `delete_document_points()`,
`list_document_chunks()`, `get_chunks_by_refs()`, and `update_document_acl()` —
all in this module. The adversarial suite in tests/isolation/ exists to catch
any regression here.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from uuid import UUID

import structlog
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import WorkspaceAccessDenied
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
        for field in ("tenant_id", "workspace_id", "document_id", "acl_groups"):
            await client.create_payload_index(
                COLLECTION, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )
    else:
        # Heal collections created before Phase 2: acl_groups gains its keyword
        # index on first touch (create_payload_index is idempotent in Qdrant).
        await client.create_payload_index(
            COLLECTION, field_name="acl_groups", field_schema=models.PayloadSchemaType.KEYWORD
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


_SCROLL_PAGE = 256
_SCROLL_PAGE_CAP = 40  # defensive cap on pages scrolled per document in get_chunks_by_refs


async def list_document_chunks(
    ctx: TenantContext, workspace_id: UUID, document_id: UUID
) -> list[RetrievedChunk]:
    """All chunks of one document in (page, chunk_index) order — the pinned-
    document read path (gap G3). Runs under the SAME tenant filter as
    retrieve(); a document outside the caller's org/workspace scrolls to
    nothing. score=1.0 marks always-present (pinned) context. Callers must
    already hold workspace access (documents.list_pinned_documents gates);
    this function ALSO enforces the same membership rule in-reader (defense
    in depth, mirrors retrieve()'s gate) — a "user"-role ctx not a member of
    workspace_id is rejected before the filter is even built."""
    if ctx.role == "user" and workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        return []
    flt = _tenant_filter(
        org_id=ctx.org_id, workspace_id=workspace_id, document_id=document_id
    )
    chunks: list[RetrievedChunk] = []
    offset: models.ExtendedPointId | None = None
    while True:
        points, offset = await client.scroll(
            COLLECTION, scroll_filter=flt, limit=_SCROLL_PAGE,
            offset=offset, with_payload=True,
        )
        for p in points:
            payload = p.payload or {}
            chunks.append(
                RetrievedChunk(
                    document_id=UUID(str(payload["document_id"])),
                    page=int(payload["page"]),
                    chunk_index=int(payload["chunk_index"]),
                    text=str(payload["text"]),
                    score=1.0,
                )
            )
        if offset is None:
            break
    chunks.sort(key=lambda c: (c.page, c.chunk_index))
    return chunks


def _parse_chunk_ref(ref: str) -> tuple[UUID, int, int] | None:
    parts = ref.split(":")
    if len(parts) != 3:
        return None
    try:
        return UUID(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


async def get_chunks_by_refs(
    ctx: TenantContext, workspace_id: UUID, refs: Sequence[str]
) -> list[RetrievedChunk]:
    """Resolve persisted citation chunk_refs ("{document_id}:{page}:{chunk_index}")
    back to chunk payloads — the citation-backfill read path (fillSourceWindow,
    gap G3/B3). Every lookup runs under the SAME tenant filter as retrieve():
    refs pointing at another org or workspace scroll to nothing and silently
    drop, as do malformed refs. Result is in ref order, deduped. score=0.0
    marks backfilled context. Callers must already hold workspace access (the
    chat service gates via get_chat + the workspace load in the same turn);
    this function ALSO enforces the same membership rule in-reader (defense
    in depth, mirrors retrieve()'s gate) — a "user"-role ctx not a member of
    workspace_id is rejected before any scroll is issued."""
    if ctx.role == "user" and workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        return []
    parsed: list[tuple[UUID, int, int]] = []
    seen_keys: set[tuple[UUID, int, int]] = set()
    for ref in refs:
        key = _parse_chunk_ref(ref)
        if key is not None and key not in seen_keys:
            seen_keys.add(key)
            parsed.append(key)
    found: dict[tuple[UUID, int, int], RetrievedChunk] = {}
    wanted_indices: dict[UUID, set[tuple[int, int]]] = {}
    for doc_id, page, chunk_index in parsed:
        wanted_indices.setdefault(doc_id, set()).add((page, chunk_index))
    for doc_id, wanted in wanted_indices.items():
        flt = _tenant_filter(
            org_id=ctx.org_id, workspace_id=workspace_id, document_id=doc_id
        )
        offset: models.ExtendedPointId | None = None
        for _ in range(_SCROLL_PAGE_CAP):
            points, offset = await client.scroll(
                COLLECTION, scroll_filter=flt, limit=_SCROLL_PAGE,
                offset=offset, with_payload=True,
            )
            for p in points:
                payload = p.payload or {}
                chunk = RetrievedChunk(
                    document_id=UUID(str(payload["document_id"])),
                    page=int(payload["page"]),
                    chunk_index=int(payload["chunk_index"]),
                    text=str(payload["text"]),
                    score=0.0,
                )
                found[(chunk.document_id, chunk.page, chunk.chunk_index)] = chunk
            found_for_doc = {
                (p, ci) for (d, p, ci) in found if d == doc_id
            }
            if offset is None or found_for_doc >= wanted:
                break
    return [found[key] for key in parsed if key in found]


class RetrievalChunkReader:
    """Default ChunkReader implementation for the chat service seam (Task 9).
    Thin bound wrapper so tests can inject a fake with the same shape."""

    async def list_document_chunks(
        self, ctx: TenantContext, workspace_id: UUID, document_id: UUID
    ) -> list[RetrievedChunk]:
        return await list_document_chunks(ctx, workspace_id, document_id)

    async def get_chunks_by_refs(
        self, ctx: TenantContext, workspace_id: UUID, refs: Sequence[str]
    ) -> list[RetrievedChunk]:
        return await get_chunks_by_refs(ctx, workspace_id, refs)


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


async def update_document_acl(
    org_id: UUID, document_id: UUID, acl_group_ids: list[UUID] | None
) -> None:
    """ACL re-index for already-indexed points (RBAC-5): rewrites the acl_groups
    payload in place via set_payload — no re-embed. Lives here so payload/filter
    knowledge never leaves this module (iron rule 1). A missing collection means
    nothing indexed yet; the ingestion pipeline will stamp the ACL at upsert."""
    if not await get_qdrant().collection_exists(COLLECTION):
        return
    await get_qdrant().set_payload(
        COLLECTION,
        payload={"acl_groups": sorted(str(g) for g in (acl_group_ids or []))},
        points=models.FilterSelector(
            filter=_tenant_filter(org_id=org_id, document_id=document_id)
        ),
        wait=True,
    )
