"""Qdrant search code paths (iron rule 1: one code path PER STORE).

`_tenant_filter` is the only function allowed to construct a filter against
the MAIN per-workspace documents collection (`COLLECTION`). Its only callers
are `retrieve()`, `delete_document_points()`, `list_document_chunks()`,
`get_chunks_by_refs()`, `update_document_acl()`, `update_document_current()`,
and `update_document_metadata()` — all in this module. Its ACL, current-only,
and metadata postures are decided per caller — see the caller table in its
docstring.

`_attachment_filter` (below) is a SECOND, deliberately separate sanctioned
filter function for a SEPARATE store — the ephemeral per-chat attachments
collection (`EPHEMERAL_COLLECTION`). It exists because that collection has a
fundamentally different access model: "visible to this one chat, to whoever
can already see that chat" is not a workspace-membership or ACL-group
question, so bolting a chat_id/ephemeral branch onto `_tenant_filter` would
make one function serve two different security models behind one signature.
Its only callers are `search_ephemeral_attachments()` and
`delete_ephemeral_points()`, both in this module.

The adversarial suite in tests/isolation/ exists to catch any regression in
EITHER filter function — `test_tenant_isolation.py`-style tests for
`_tenant_filter`, `test_ephemeral_attachment_isolation.py` for
`_attachment_filter`.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import UUID, uuid5

import structlog
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import WorkspaceAccessDenied
from raghub.modules.documents.pipeline import Chunk
from raghub.modules.retrieval.client import COLLECTION, EPHEMERAL_COLLECTION, get_qdrant
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
    section: str | None = None
    version: int = 1


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    no_answer: bool


@dataclass(frozen=True)
class MetadataClause:
    """Declarative metadata condition (DOC-6). Built ONLY by
    modules/documents/metadata.py::build_clauses (owns field-type knowledge);
    consumed ONLY by _tenant_filter (owns filter syntax). `key` is always
    'meta.'-prefixed by its builder — user input can never target tenant keys."""

    key: str
    kind: str  # "eq" | "date_range"
    value: str | None = None
    gte: str | None = None  # RFC 3339 / ISO date
    lte: str | None = None


def _parse_iso(value: str | None) -> datetime | date | None:
    """MetadataClause carries RFC 3339 / ISO date strings (declarative,
    builder-agnostic); DatetimeRange wants datetime|date objects."""
    return datetime.fromisoformat(value) if value is not None else None


def _tenant_filter(
    *,
    org_id: UUID,
    workspace_id: UUID | None = None,
    document_id: UUID | None = None,
    acl_group_ids: frozenset[UUID] | None,
    current_only: bool,
    metadata_clauses: Sequence[MetadataClause] | None,
) -> models.Filter:
    """The ONE Qdrant filter builder (iron rule 1). tenant_id is always a
    must-condition. acl_group_ids, current_only, and metadata_clauses are all
    REQUIRED (no defaults) so every caller states its full posture explicitly:

    | caller | acl_group_ids | current_only | metadata_clauses |
    |---|---|---|---|
    | `retrieve()` | `_ctx_acl(ctx)` | `True` | caller param |
    | `list_document_chunks()` | `_ctx_acl(ctx)` | `False` (pinned doc served mid-swap) | `None` |
    | `get_chunks_by_refs()` | `_ctx_acl(ctx)` | `False` (citation backfill resolves) | `None` |
    | `delete_document_points()` | `None` | `False` | `None` |
    | `update_document_acl()` | `None` | `False` | `None` |
    | `update_document_current()` | `None` | `False` | `None` |
    | `update_document_metadata()` | `None` | `False` | `None` |

      * acl_group_ids: None -> no ACL clause. Sanctioned for admin+ retrieval
        (RBAC-5 restricts users, not the admins who manage the library) and
        for maintenance paths already scoped tenant+document. frozenset ->
        nested must-clause: acl_groups IS EMPTY (unrestricted; also matches
        every pre-Phase-2 point, ingested as []) OR intersects the caller's
        groups. An empty set emits IsEmpty only — fail closed, never
        MatchAny(any=[]).
      * current_only: True -> nested must-clause: is_current == true OR the
        is_current key is absent entirely (legacy pre-H points — safe because
        promotion *deletes* demoted points, so an existing keyless point can
        only belong to a current version; no payload backfill job needed).
        False -> no current-only clause (maintenance paths and pinned-document/
        citation reads that must keep resolving mid-version-swap).
      * metadata_clauses: per-clause eq (MatchValue) or date_range
        (DatetimeRange) FieldConditions, all top-level must-conditions (no
        nesting needed — every clause narrows, none widen).
    """
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
    if acl_group_ids is not None:
        acl_should: list[models.Condition] = [
            models.IsEmptyCondition(is_empty=models.PayloadField(key="acl_groups"))
        ]
        if acl_group_ids:
            acl_should.append(
                models.FieldCondition(
                    key="acl_groups",
                    match=models.MatchAny(any=sorted(str(g) for g in acl_group_ids)),
                )
            )
        must.append(models.Filter(should=acl_should))
    if current_only:
        must.append(
            models.Filter(
                should=[
                    models.FieldCondition(
                        key="is_current", match=models.MatchValue(value=True)
                    ),
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="is_current")),
                ]
            )
        )
    for clause in metadata_clauses or ():
        if clause.kind == "eq":
            must.append(
                models.FieldCondition(
                    key=clause.key, match=models.MatchValue(value=clause.value or "")
                )
            )
        elif clause.kind == "date_range":
            must.append(
                models.FieldCondition(
                    key=clause.key,
                    range=models.DatetimeRange(
                        gte=_parse_iso(clause.gte), lte=_parse_iso(clause.lte)
                    ),
                )
            )
        else:  # pragma: no cover - MetadataClause is built by one function
            raise ValueError(f"unknown metadata clause kind: {clause.kind}")
    return models.Filter(must=must)


def _ctx_acl(ctx: TenantContext) -> frozenset[UUID] | None:
    """ACL posture for user-facing reads: admins bypass (documented RBAC-5
    stance), everyone else carries their current group memberships."""
    return None if ctx.role in ("admin", "superadmin") else ctx.group_ids


_HEALED: set[str] = set()  # collections whose Plan H heal indexes have run this process


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
        await client.create_payload_index(
            COLLECTION, field_name="is_current", field_schema=models.PayloadSchemaType.BOOL
        )
    else:
        # Heal collections created before Phase 2/H: acl_groups and is_current
        # gain their indexes on first touch (create_payload_index is idempotent
        # in Qdrant). Gated to once per process (Plan K carried finding) —
        # every subsequent retrieve() call hit this branch and re-issued both
        # calls needlessly.
        if COLLECTION not in _HEALED:
            await client.create_payload_index(
                COLLECTION, field_name="acl_groups",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                COLLECTION, field_name="is_current", field_schema=models.PayloadSchemaType.BOOL
            )
            _HEALED.add(COLLECTION)
    return COLLECTION


async def ensure_ephemeral_collection() -> str:
    """Idempotent setup for the SEPARATE ephemeral-attachments store (not the
    main documents collection). No embedding-model lock — attachments always
    use whatever the deployment's single dense embedder is (get_dense_embedder()),
    same as everything else; there is no per-workspace choice here."""
    client = get_qdrant()
    if not await client.collection_exists(EPHEMERAL_COLLECTION):
        await client.create_collection(
            EPHEMERAL_COLLECTION,
            vectors_config={
                "dense": models.VectorParams(
                    size=get_settings().embedding_dim, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for field in ("tenant_id", "chat_id"):
            await client.create_payload_index(
                EPHEMERAL_COLLECTION, field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
    return EPHEMERAL_COLLECTION


def _attachment_filter(*, org_id: UUID, chat_id: UUID) -> models.Filter:
    """The SECOND sanctioned filter-building function (see module
    docstring). No ACL, no workspace, no current-only posture — an
    ephemeral attachment's only access rule is 'this org, this chat.'"""
    return models.Filter(
        must=[
            models.FieldCondition(
                key="tenant_id", match=models.MatchValue(value=str(org_id))
            ),
            models.FieldCondition(
                key="chat_id", match=models.MatchValue(value=str(chat_id))
            ),
        ]
    )


# Distinct from documents/pipeline.py's _CHUNK_NAMESPACE and _HQ_NAMESPACE —
# ephemeral attachment points must never collide with main-collection points
# even if a UUID were somehow reused across stores.
_EPHEMERAL_NAMESPACE = UUID("0f261cc1-a520-4fe5-9250-5271a316c23d")


async def upsert_ephemeral_chunks(
    *, org_id: UUID, chat_id: UUID, attachment_id: UUID,
    chunks: list["Chunk"], dense: list[list[float]], sparse: list[models.SparseVector],
) -> None:
    """Constructs points, never filters (same convention documents/pipeline.py's
    upsert_points already uses — writing has no filter to centralize)."""
    points = [
        models.PointStruct(
            id=str(uuid5(_EPHEMERAL_NAMESPACE, f"{attachment_id}:{c.chunk_index}")),
            vector={"dense": d, "sparse": s},
            payload={
                "tenant_id": str(org_id), "chat_id": str(chat_id),
                "attachment_id": str(attachment_id),
                "page": c.page, "chunk_index": c.chunk_index, "text": c.text,
            },
        )
        for c, d, s in zip(chunks, dense, sparse, strict=True)
    ]
    await get_qdrant().upsert(EPHEMERAL_COLLECTION, points=points, wait=True)


async def search_ephemeral_attachments(
    *, org_id: UUID, chat_id: UUID, query_dense: list[float],
    query_sparse: models.SparseVector, top_k: int = 5,
) -> list[RetrievedChunk]:
    """Hybrid dense+sparse RRF fusion, same fusion shape `retrieve()` already
    uses for the main collection (Prefetch both, fuse with RRF) — no rerank
    step, since that's a per-workspace-setting concept that doesn't apply to
    an ephemeral per-chat store."""
    flt = _attachment_filter(org_id=org_id, chat_id=chat_id)
    result = await get_qdrant().query_points(
        EPHEMERAL_COLLECTION,
        prefetch=[
            models.Prefetch(query=query_dense, using="dense", filter=flt, limit=top_k * 4),
            models.Prefetch(query=query_sparse, using="sparse", filter=flt, limit=top_k * 4),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=flt,
        limit=top_k,
        with_payload=True,
    )
    chunks: list[RetrievedChunk] = []
    for p in result.points:
        payload = p.payload or {}
        chunks.append(
            RetrievedChunk(
                document_id=UUID(str(payload["attachment_id"])),
                page=int(payload["page"]),
                chunk_index=int(payload["chunk_index"]),
                text=str(payload["text"]),
                score=float(p.score),
            )
        )
    return chunks


async def delete_ephemeral_points(chat_id: UUID, attachment_ids: Sequence[UUID]) -> None:
    """Deletes ephemeral Qdrant points for ONLY the given attachment(s) within
    one chat (whole-branch review fix, DOC-9): the delete filter is chat_id
    AND attachment_id-in-(...), never chat_id alone. Without the attachment_id
    clause, a TTL sweep cleaning up a chat's stale (>24h) attachments would
    also wipe a sibling attachment in the SAME chat that hasn't hit its own
    24h TTL yet -- its DB row and MinIO blob would survive while its vectors
    silently vanished early. attachment_ids is required, not optional:
    cleanup_stale_attachments_task (worker/tasks.py) is this function's only
    caller, so there's no other caller to stay backward-compatible with, and
    no reason to keep an unscoped whole-chat-delete mode alive. An empty
    sequence is a no-op -- a sweep run with no stale attachments in this chat
    has nothing to delete."""
    if not attachment_ids:
        return
    await get_qdrant().delete(
        EPHEMERAL_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="chat_id", match=models.MatchValue(value=str(chat_id))
                    ),
                    models.FieldCondition(
                        key="attachment_id",
                        match=models.MatchAny(any=sorted(str(a) for a in attachment_ids)),
                    ),
                ]
            )
        ),
        wait=True,
    )


_RERANK_PREFETCH = 50  # CHAT-2: rerank the top-50 fused candidates


def _dedupe_hq(candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Collapse {parent, hq×N} groups sharing (document_id, page,
    chunk_index) into one RetrievedChunk carrying the max score (spec §4:
    "retrieval dedupes hq hits into their parent chunk"). Runs on Qdrant's
    already-fused candidate list — Qdrant's RRF fusion (K-C2) is one opaque
    server call with no earlier client-side hook, so this is the earliest
    point a chunk_ref-level merge is possible. Order-preserving: keeps each
    group's first-seen position, never re-ranks what fusion already ranked."""
    best: dict[tuple[UUID, int, int], RetrievedChunk] = {}
    order: list[tuple[UUID, int, int]] = []
    for c in candidates:
        key = (c.document_id, c.page, c.chunk_index)
        if key not in best:
            order.append(key)
            best[key] = c
        elif c.score > best[key].score:
            best[key] = c
    return [best[key] for key in order]


def _chunk_from_point(point: models.ScoredPoint) -> RetrievedChunk:
    payload = point.payload or {}
    return RetrievedChunk(
        document_id=UUID(str(payload["document_id"])),
        page=int(payload["page"]),
        chunk_index=int(payload["chunk_index"]),
        text=str(payload["text"]),
        score=float(point.score),
        section=payload.get("section"),
        version=int(payload.get("version", 1)),
    )


async def retrieve(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    query: str,
    top_k: int | None = None,
    metadata_clauses: Sequence[MetadataClause] | None = None,
) -> RetrievalResult:
    """Hybrid retrieval — the one code path (spec §3.3), Plan E additions:

    1. Workspace access gate (typed WorkspaceAccessDenied).
    2. top_k=None resolves to workspace.top_k (ADM-3).
    3. Qdrant prefetch dense + sparse under the tenant filter → RRF fusion
       (top-50 candidates when workspace.rerank_enabled, else top_k).
       Plan K Task 5: fused candidates are then deduped by (document_id,
       page, chunk_index) via `_dedupe_hq` — collapses a chunk's own point
       and its hq siblings into one RetrievedChunk (max score wins) before
       any no_answer/top_k decision. No-op when no hq points exist.
    4. rerank_enabled: cross-encoder scores the candidates; final top_k come
       back in reranker order carrying RERANKER scores, and no_answer compares
       the best reranker score against workspace.min_score. CALIBRATION: that
       threshold now reads in sigmoid cross-encoder space, not dense-cosine
       space — revisit min_score when flipping rerank_enabled.
    5. Reranker down → structlog warning and EXACTLY the pre-rerank behavior:
       fusion order, no_answer via best dense cosine (NFR graceful degradation).
    6. Plan H: current_only=True — only the current version of each document
       (or legacy pre-H points) is ever retrievable; metadata_clauses narrow
       further per Task 10's route wiring.
    """
    ws = await get_workspace_checked(session, ctx, workspace_id)
    k = top_k if top_k is not None else ws.top_k
    await ensure_collection(ws.embedding_model)
    dense_vec = (await get_dense_embedder().embed([query]))[0]
    sparse_vec = (await asyncio.to_thread(embed_sparse, [query]))[0]
    flt = _tenant_filter(
        org_id=ctx.org_id, workspace_id=workspace_id, acl_group_ids=_ctx_acl(ctx),
        current_only=True, metadata_clauses=metadata_clauses,
    )
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
    candidates = _dedupe_hq(candidates)
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
        org_id=ctx.org_id, workspace_id=workspace_id, document_id=document_id,
        acl_group_ids=_ctx_acl(ctx), current_only=False, metadata_clauses=None,
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
                    section=payload.get("section"),
                    version=int(payload.get("version", 1)),
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
            org_id=ctx.org_id, workspace_id=workspace_id, document_id=doc_id,
            acl_group_ids=_ctx_acl(ctx), current_only=False, metadata_clauses=None,
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
                    section=payload.get("section"),
                    version=int(payload.get("version", 1)),
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
            # maintenance path: must remove ALL of the document's points
            filter=_tenant_filter(
                org_id=org_id, document_id=document_id,
                acl_group_ids=None, current_only=False, metadata_clauses=None,
            )
        ),
        wait=True,
    )


async def update_document_acl(
    org_id: UUID, document_id: UUID, acl_group_ids: list[UUID] | None
) -> None:
    """ACL re-index for already-indexed points (RBAC-5): rewrites the acl_groups
    payload in place via set_payload — no re-embed. Lives here so payload/filter
    knowledge never leaves this module (iron rule 1). A missing collection means
    nothing indexed yet; the ingestion pipeline will stamp the ACL at upsert.

    Invariant: acl_group_ids is None === unrestricted, encoded here as an empty
    payload list (sorted(str(g) for g in (acl_group_ids or []))) — the point
    carries no acl_groups values, so `_tenant_filter`'s future acl intersection
    check matches unconditionally. A CALLER passing `[]` for "restricted to no
    groups" would collide with this exact encoding, which is why `[]` is
    rejected at the AclUpdate schema layer before it ever reaches this
    function or Document.acl_group_ids."""
    if not await get_qdrant().collection_exists(COLLECTION):
        return
    await get_qdrant().set_payload(
        COLLECTION,
        payload={"acl_groups": sorted(str(g) for g in (acl_group_ids or []))},
        points=models.FilterSelector(
            # maintenance path: must restamp ALL of the document's points
            filter=_tenant_filter(
                org_id=org_id, document_id=document_id,
                acl_group_ids=None, current_only=False, metadata_clauses=None,
            )
        ),
        wait=True,
    )


async def update_document_current(org_id: UUID, document_id: UUID, *, is_current: bool) -> None:
    """Promotion/demotion visibility flip — set_payload under the tenant filter
    (mirrors update_document_acl). No-op when the collection doesn't exist."""
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        return
    await client.set_payload(
        COLLECTION,
        payload={"is_current": is_current},
        points=models.FilterSelector(
            filter=_tenant_filter(
                org_id=org_id, document_id=document_id,
                acl_group_ids=None, current_only=False, metadata_clauses=None,
            )
        ),
        wait=True,
    )


async def update_document_metadata(org_id: UUID, document_id: UUID, meta: dict[str, str]) -> None:
    """Metadata-value payload mirror for already-indexed points (DOC-6):
    rewrites the nested `meta` payload key in place via set_payload — no
    re-embed. Lives here so payload/filter knowledge never leaves this module
    (iron rule 1); mirrors update_document_acl/update_document_current's
    shape. A missing collection means nothing indexed yet; the ingestion
    pipeline stamps `meta` at upsert (pipeline.upsert_points)."""
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        return
    await client.set_payload(
        COLLECTION,
        payload={"meta": meta},
        points=models.FilterSelector(
            # maintenance path: must restamp ALL of the document's points
            filter=_tenant_filter(
                org_id=org_id, document_id=document_id,
                acl_group_ids=None, current_only=False, metadata_clauses=None,
            )
        ),
        wait=True,
    )


async def ensure_metadata_index(field_name: str, field_type: str) -> None:
    """Payload index for a workspace metadata field (DOC-6). Index creation is
    payload-schema work, not filtering — it lives here so no other module
    touches Qdrant, but it constructs no filters (iron rule 1 untouched)."""
    client = get_qdrant()
    if not await client.collection_exists(COLLECTION):
        return
    schema = (
        models.PayloadSchemaType.DATETIME
        if field_type == "date"
        else models.PayloadSchemaType.KEYWORD
    )
    await client.create_payload_index(
        COLLECTION, field_name=f"meta.{field_name}", field_schema=schema
    )
