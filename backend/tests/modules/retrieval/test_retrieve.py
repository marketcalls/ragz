import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import get_settings
from ragz.core.errors import UpstreamError, WorkspaceAccessDenied
from ragz.modules.auth.models import User
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from ragz.modules.retrieval import service as retrieval_service
from ragz.modules.retrieval.client import COLLECTION, get_qdrant
from ragz.modules.retrieval.embeddings import (
    InMemoryQueryEmbeddingCache,
    clear_query_embedding_cache,
    embed_sparse,
    get_dense_embedder,
)
from ragz.modules.retrieval.query_expansion import ExpandedQueries
from ragz.modules.retrieval.service import (
    RetrievedChunk,
    _capture_stage,
    _dedupe_hq,
    _embed_query_batch,
    _stable_chunk_order,
    _stable_rerank_order,
    delete_document_points,
    ensure_collection,
    retrieve,
)
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization, Workspace, WorkspaceMember

# The dense embedder used by test-seeded points (upsert_texts and friends):
# always resolves to HashDenseEmbedder under RAGZ_EMBEDDING_BACKEND=hash
# (stack_env), but get_dense_embedder's signature (DOC-10, Task 2) now
# requires a model identity regardless of backend -- these args mirror the
# LOCAL_EMBEDDING_MODEL_ID row the shared `engine` fixture seeds.
_LOCAL_MODEL_KW = {
    "model_id": LOCAL_EMBEDDING_MODEL_ID,
    "provider_kind": "tei",
    "litellm_model_name": "local-embeddings",
}


def test_atomic_stage_timing_records_failure_without_sensitive_context() -> None:
    timings: dict[str, float] = {}

    with pytest.raises(RuntimeError, match="synthetic failure"):
        with _capture_stage(timings, "failed_stage"):
            raise RuntimeError("synthetic failure")

    assert timings.keys() == {"failed_stage"}
    assert timings["failed_stage"] >= 0


def test_equal_score_chunks_and_reranks_have_deterministic_secondary_order() -> None:
    first_id = uuid4()
    second_id = uuid4()
    low, high = sorted((first_id, second_id), key=str)
    chunks = [
        RetrievedChunk(second_id, 2, 0, "second", 0.5),
        RetrievedChunk(first_id, 1, 0, "first", 0.5),
    ]

    ordered = _stable_chunk_order(chunks)
    assert [chunk.document_id for chunk in ordered] == [low, high]
    assert _stable_rerank_order([0.7, 0.7], chunks) == (
        [0, 1] if second_id == low else [1, 0]
    )


async def test_cancelled_embedding_cache_owner_accounts_before_waiter_reuse() -> None:
    cache = InMemoryQueryEmbeddingCache(max_entries=4)
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()
    usage_recorded = asyncio.Event()
    recorded: list[int] = []

    class BilledEmbedder:
        async def embed_with_usage(self, texts: list[str]):  # type: ignore[no-untyped-def]
            provider_started.set()
            await release_provider.wait()
            return [[1.0, 0.0] for _ in texts], 7

    async def record_usage(tokens: int) -> None:
        recorded.append(tokens)
        usage_recorded.set()

    async def run() -> object:
        return await _embed_query_batch(
            queries=("same query",),
            dense_embedder=BilledEmbedder(),
            query_cache=cache,
            cache_namespace="org-model",
            expected_dimension=2,
            stage_timings_ms=None,
            record_billed_usage=record_usage,
        )

    owner = asyncio.create_task(run())
    await provider_started.wait()
    waiter = asyncio.create_task(run())
    await cache._lock.acquire()  # noqa: SLF001 - deterministic publication boundary
    release_provider.set()
    await usage_recorded.wait()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    cache._lock.release()  # noqa: SLF001

    await asyncio.wait_for(waiter, timeout=1)
    assert recorded == [7]


async def test_billed_malformed_embedding_is_accounted_before_local_validation() -> None:
    recorded: list[int] = []

    class MalformedBilledEmbedder:
        async def embed_with_usage(self, texts: list[str]):  # type: ignore[no-untyped-def]
            return [[1.0] for _ in texts], 9

    async def record(tokens: int) -> None:
        recorded.append(tokens)

    with pytest.raises(UpstreamError, match="wrong vector width"):
        await _embed_query_batch(
            queries=("question",), dense_embedder=MalformedBilledEmbedder(),
            query_cache=None, cache_namespace="org-model", expected_dimension=2,
            stage_timings_ms=None, record_billed_usage=record,
        )
    assert recorded == [9]


async def seed_workspace(
    session: AsyncSession, org_name: str, *, role: str = "user", member: bool = True,
    min_score: float = 0.0, top_k: int = 8, rerank_enabled: bool = False,
    multi_query_enabled: bool = False,
) -> tuple[TenantContext, Workspace]:
    org = Organization(name=org_name)
    session.add(org)
    await session.flush()
    ws = Workspace(
        org_id=org.id,
        name="ws",
        min_score=min_score,
        top_k=top_k,
        rerank_enabled=rerank_enabled,
        multi_query_enabled=multi_query_enabled,
    )
    user = User(org_id=org.id, email=f"u@{org_name}.com", password_hash="x", role=role)  # noqa: S106
    session.add_all([ws, user])
    await session.flush()
    if member:
        session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.commit()
    ctx = TenantContext(
        user_id=user.id, org_id=org.id, role=role,
        workspace_ids=frozenset({ws.id}) if member else frozenset(),
    )
    return ctx, ws


async def upsert_texts(
    ctx: TenantContext, ws: Workspace, texts: list[str],
    point_ids: list[str] | None = None,
) -> str:
    """Test seeding via raw points; production code goes through the pipeline.

    point_ids: optional deterministic ids — Qdrant scroll order is point-id order,
    so tests asserting page positions must pass sequential ids (see early-stop test).
    """
    document_id = str(uuid4())
    dense = await get_dense_embedder(**_LOCAL_MODEL_KW).embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, texts)
    points = [
        models.PointStruct(
            id=point_ids[i] if point_ids else str(uuid4()),
            vector={"dense": d, "sparse": s},
            payload={"tenant_id": str(ctx.org_id), "workspace_id": str(ws.id),
                     "document_id": document_id, "page": i + 1, "chunk_index": i,
                     "text": t, "doc_type": "text/plain", "date": "2026-07-18",
                     "acl_groups": []},
        )
        for i, (t, d, s) in enumerate(zip(texts, dense, sparse, strict=True))
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
    return document_id


class _FakeQueryExpander:
    def __init__(
        self,
        alternatives: tuple[str, ...] = ("expanded alpha", "expanded beta"),
        *,
        error: Exception | None = None,
    ) -> None:
        self.alternatives = alternatives
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def expand(self, query: str, *, model: str) -> ExpandedQueries:
        self.calls.append((query, model))
        if self.error is not None:
            raise self.error
        return ExpandedQueries(
            queries=(query, *self.alternatives),
            prompt_tokens=13,
            completion_tokens=5,
        )


async def test_retrieve_returns_matching_chunk(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orga")
    await upsert_texts(ctx, ws, ["the flux capacitor requires 1.21 gigawatts",
                                 "unrelated kumquat farming notes"])
    result = await retrieve(session, ctx, ws.id, "flux capacitor gigawatts", top_k=2)
    assert not result.no_answer
    assert result.chunks[0].text.startswith("the flux capacitor")
    assert result.chunks[0].page == 1 and result.chunks[0].chunk_index == 0


async def test_min_score_triggers_no_answer_with_nearest(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgb", min_score=0.99)
    await upsert_texts(ctx, ws, ["some vaguely related text about invoices"])
    result = await retrieve(session, ctx, ws.id, "completely different query terms")
    assert result.no_answer
    assert result.chunks  # nearest sources still surfaced (CHAT-9)


async def test_no_answer_probe_overlaps_fused_vector_search(
    session: AsyncSession,
    qdrant_collection: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, ws = await seed_workspace(session, "probe-overlap")
    await upsert_texts(ctx, ws, ["alpha report"])
    client = get_qdrant()
    original = client.query_points
    fused_started = asyncio.Event()
    probe_started = asyncio.Event()

    async def observed_query_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("prefetch") is not None:
            fused_started.set()
            await asyncio.wait_for(probe_started.wait(), timeout=1)
        elif kwargs.get("using") == "dense" and kwargs.get("limit") == 1:
            probe_started.set()
            await asyncio.wait_for(fused_started.wait(), timeout=1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(client, "query_points", observed_query_points)
    result = await asyncio.wait_for(
        retrieve(session, ctx, ws.id, "alpha"), timeout=2
    )

    assert result.chunks
    assert fused_started.is_set() and probe_started.is_set()


async def test_query_embedding_cache_reports_cold_miss_then_warm_hit(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "query-cache")
    await upsert_texts(ctx, ws, ["alpha report", "unrelated notes"])
    cache = InMemoryQueryEmbeddingCache()

    cold = await retrieve(
        session, ctx, ws.id, "alpha", query_embedding_cache=cache
    )
    warm = await retrieve(
        session, ctx, ws.id, "alpha", query_embedding_cache=cache
    )

    assert (cold.embedding_cache_hits, cold.embedding_cache_misses) == (0, 1)
    assert (warm.embedding_cache_hits, warm.embedding_cache_misses) == (1, 0)
    assert [chunk.document_id for chunk in warm.chunks] == [
        chunk.document_id for chunk in cold.chunks
    ]


async def test_configured_query_embedding_cache_is_used_and_can_be_overridden_off(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, ws = await seed_workspace(session, "configured-query-cache")
    await upsert_texts(ctx, ws, ["alpha report", "unrelated notes"])
    settings = get_settings().model_copy(
        update={
            "query_embedding_cache_enabled": True,
            "query_embedding_cache_max_entries": 10,
            "query_embedding_cache_ttl_seconds": 60,
        }
    )
    clear_query_embedding_cache()
    monkeypatch.setattr(retrieval_service, "get_settings", lambda: settings)

    cold = await retrieve(session, ctx, ws.id, "alpha")
    warm = await retrieve(session, ctx, ws.id, "alpha")
    bypassed = await retrieve(
        session,
        ctx,
        ws.id,
        "alpha",
        query_embedding_cache_enabled_override=False,
    )

    assert (cold.embedding_cache_hits, cold.embedding_cache_misses) == (0, 1)
    assert (warm.embedding_cache_hits, warm.embedding_cache_misses) == (1, 0)
    assert (bypassed.embedding_cache_hits, bypassed.embedding_cache_misses) == (0, 1)
    clear_query_embedding_cache()


async def test_query_embedding_cache_wrong_width_fails_before_vector_search(
    session: AsyncSession, qdrant_collection: None
) -> None:
    class WrongWidthCache:
        async def get_many(self, _namespace, texts):  # type: ignore[no-untyped-def]
            return [[1.0, 2.0] for _text in texts]

        async def set_many(self, _namespace, _texts, _vectors):  # type: ignore[no-untyped-def]
            raise AssertionError("a cache hit must not be stored again")

        async def get_or_compute(self, _namespace, texts, _compute):  # type: ignore[no-untyped-def]
            return [[1.0, 2.0] for _text in texts], 0, len(texts), 0, 0.0, 0.0, 0.0

    ctx, ws = await seed_workspace(session, "wrong-width-query-cache")
    await upsert_texts(ctx, ws, ["alpha report"])

    with pytest.raises(UpstreamError, match="wrong vector width"):
        await retrieve(
            session,
            ctx,
            ws.id,
            "alpha",
            query_embedding_cache=WrongWidthCache(),
        )


async def test_empty_workspace_is_no_answer(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgc")
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.no_answer and result.chunks == []


async def test_disabled_multi_query_never_calls_expander(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "mq-disabled")
    await upsert_texts(ctx, ws, ["alpha report"])
    expander = _FakeQueryExpander()

    result = await retrieve(
        session, ctx, ws.id, "alpha", query_expander=expander
    )

    assert result.chunks
    assert expander.calls == []


async def test_enabled_multi_query_without_utility_model_uses_original_only(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(
        session, "mq-no-utility", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    expander = _FakeQueryExpander()

    result = await retrieve(
        session, ctx, ws.id, "alpha", query_expander=expander
    )

    assert result.chunks
    assert expander.calls == []


async def test_enabled_multi_query_builds_six_filtered_prefetches_and_records_usage(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from ragz.modules.quotas.models import UsageRecord

    ctx, ws = await seed_workspace(
        session, "mq-lanes", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha beta report", "unrelated text"])
    expander = _FakeQueryExpander(("alpha report", "beta report"))
    client = get_qdrant()
    original_query_points = client.query_points
    captured_prefetches: list[models.Prefetch] = []

    async def spy_query_points(*args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("prefetch") is not None:
            captured_prefetches.extend(kwargs["prefetch"])
        return await original_query_points(*args, **kwargs)

    monkeypatch.setattr(client, "query_points", spy_query_points)
    stage_timings_ms: dict[str, float] = {}

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "alpha beta",
        query_expander=expander,
        stage_timings_ms=stage_timings_ms,
    )

    assert result.chunks
    assert expander.calls == [("alpha beta", "utility-model")]
    assert len(captured_prefetches) == 6
    assert all(prefetch.filter is not None for prefetch in captured_prefetches)
    assert {
        "workspace_model_resolution",
        "database_release",
        "collection_ready",
        "query_expansion",
        "dense_embedding",
        "sparse_embedding",
        "authorization_prefilter",
        "vector_search",
        "authorization_recheck",
        "no_answer_probe",
    } <= stage_timings_ms.keys()
    assert all(value >= 0 for value in stage_timings_ms.values())
    usage = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.feature == "query_expansion",
            )
        )
    ).scalar_one()
    assert (usage.prompt_tokens, usage.completion_tokens) == (13, 5)


async def test_default_expander_records_provider_usage_once(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, ws = await seed_workspace(
        session, "mq-provider-accounting-once", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    recorded_features: list[str] = []

    class ProviderAccountedExpander:
        def __init__(self, record_usage):  # type: ignore[no-untyped-def]
            self.record_usage = record_usage

        async def expand(self, query: str, *, model: str) -> ExpandedQueries:
            expanded = ExpandedQueries((query, "alpha report"), 13, 5)
            await self.record_usage(expanded)
            return expanded

    def build(*args, **kwargs):  # type: ignore[no-untyped-def]
        return ProviderAccountedExpander(kwargs["record_usage"])

    async def record(*args, **kwargs):  # type: ignore[no-untyped-def]
        recorded_features.append(kwargs["feature"])

    monkeypatch.setattr(retrieval_service, "build_query_expander", build)
    monkeypatch.setattr(retrieval_service.quota_service, "record_usage_durable", record)
    result = await retrieve(session, ctx, ws.id, "alpha")

    assert result.chunks
    assert recorded_features.count("query_expansion") == 1


async def test_multi_query_provider_failure_degrades_to_original(
    session: AsyncSession, qdrant_collection: None, utility_model: object
) -> None:
    ctx, ws = await seed_workspace(
        session, "mq-provider-down", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    expander = _FakeQueryExpander(error=UpstreamError("provider down"))

    result = await retrieve(
        session, ctx, ws.id, "alpha", query_expander=expander
    )

    assert result.chunks
    assert expander.calls == [("alpha", "utility-model")]


async def test_alternative_embedding_failure_keeps_q1_and_completed_usage(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from ragz.modules.quotas.models import UsageRecord

    ctx, ws = await seed_workspace(
        session, "mq-alternative-embedding-failure", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    delegate = get_dense_embedder(**_LOCAL_MODEL_KW)
    calls = 0

    class FailingAlternativeEmbedder:
        async def embed_with_usage(self, texts: list[str]):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 2:
                raise UpstreamError("alternative embedding failed")
            return await delegate.embed(texts), 7

    monkeypatch.setattr(
        retrieval_service,
        "get_dense_embedder",
        lambda *args, **kwargs: FailingAlternativeEmbedder(),
    )

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "alpha",
        query_expander=_FakeQueryExpander(("expanded alpha",)),
        query_embedding_cache_enabled_override=False,
    )

    assert result.chunks
    assert result.query_count == 1
    usage = list(
        (
            await session.execute(
                select(UsageRecord)
                .where(
                    UsageRecord.org_id == ctx.org_id,
                    UsageRecord.feature.in_(("query_expansion", "embedding")),
                )
                .order_by(UsageRecord.created_at)
            )
        ).scalars()
    )
    assert [(row.feature, row.prompt_tokens, row.completion_tokens) for row in usage] == [
        ("embedding", 7, 0),
        ("query_expansion", 13, 5),
    ]


async def test_completed_expansion_usage_survives_original_embedding_failure(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from ragz.modules.quotas.models import UsageRecord

    ctx, ws = await seed_workspace(
        session, "mq-q1-embedding-failure", multi_query_enabled=True
    )
    expansion_completed = asyncio.Event()

    class CompletedExpander(_FakeQueryExpander):
        async def expand(self, query: str, *, model: str) -> ExpandedQueries:
            result = await super().expand(query, model=model)
            expansion_completed.set()
            return result

    class FailingOriginalEmbedder:
        async def embed_with_usage(self, texts: list[str]):  # type: ignore[no-untyped-def]
            await expansion_completed.wait()
            await asyncio.sleep(0)
            raise UpstreamError("original embedding failed")

    monkeypatch.setattr(
        retrieval_service,
        "get_dense_embedder",
        lambda *args, **kwargs: FailingOriginalEmbedder(),
    )

    with pytest.raises(UpstreamError, match="original embedding failed"):
        await retrieve(
            session,
            ctx,
            ws.id,
            "alpha",
            query_expander=CompletedExpander(("expanded alpha",)),
            query_embedding_cache_enabled_override=False,
        )

    usage = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.feature == "query_expansion",
            )
        )
    ).scalar_one()
    assert (usage.prompt_tokens, usage.completion_tokens) == (13, 5)


async def test_multi_query_releases_db_transaction_before_provider_call(
    session: AsyncSession, qdrant_collection: None, utility_model: object
) -> None:
    ctx, ws = await seed_workspace(
        session, "mq-transaction-boundary", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])

    class CheckingExpander(_FakeQueryExpander):
        async def expand(self, query: str, *, model: str) -> ExpandedQueries:
            assert session.in_transaction() is False
            return await super().expand(query, model=model)

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "alpha",
        query_expander=CheckingExpander(("alpha report",)),
    )

    assert result.chunks


async def test_multi_query_starts_original_embedding_before_expansion_finishes(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, ws = await seed_workspace(
        session, "mq-speculative", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    delegate = get_dense_embedder(**_LOCAL_MODEL_KW)
    embedding_started = asyncio.Event()
    expansion_started = asyncio.Event()

    class ObservedEmbedder:
        async def embed_with_usage(self, texts: list[str]):  # type: ignore[no-untyped-def]
            embedding_started.set()
            return await delegate.embed_with_usage(texts)

    class ObservedExpander(_FakeQueryExpander):
        async def expand(self, query: str, *, model: str) -> ExpandedQueries:
            expansion_started.set()
            await asyncio.wait_for(embedding_started.wait(), timeout=1)
            return await super().expand(query, model=model)

    monkeypatch.setattr(
        retrieval_service, "get_dense_embedder", lambda *args, **kwargs: ObservedEmbedder()
    )
    result = await asyncio.wait_for(
        retrieve(
            session,
            ctx,
            ws.id,
            "alpha",
            query_expander=ObservedExpander(("alpha report",)),
        ),
        timeout=2,
    )

    assert expansion_started.is_set()
    assert embedding_started.is_set()
    assert result.query_count == 2


async def test_multi_query_timeout_cancels_expansion_and_returns_q1(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
) -> None:
    from sqlalchemy import select

    from ragz.modules.quotas.models import UsageRecord

    ctx, ws = await seed_workspace(
        session, "mq-timeout", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    cancelled = asyncio.Event()

    class NeverExpander(_FakeQueryExpander):
        async def expand(self, query: str, *, model: str) -> ExpandedQueries:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "alpha",
        query_expander=NeverExpander(),
        multi_query_expansion_timeout_ms_override=25,
    )

    assert result.query_count == 1
    assert cancelled.is_set()
    usage = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.feature == "query_expansion",
            )
        )
    ).scalar_one_or_none()
    assert usage is None


async def test_multi_query_deadline_cancels_expansion_during_original_embedding(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, ws = await seed_workspace(
        session, "mq-timeout-during-embedding", multi_query_enabled=True
    )
    await upsert_texts(ctx, ws, ["alpha report"])
    delegate = get_dense_embedder(**_LOCAL_MODEL_KW)
    cancelled = asyncio.Event()

    class SlowEmbedder:
        async def embed_with_usage(self, texts: list[str]):  # type: ignore[no-untyped-def]
            # The expansion deadline must fire while this provider call is still
            # in flight, not only after the original embedding returns.
            await asyncio.wait_for(cancelled.wait(), timeout=0.2)
            return await delegate.embed_with_usage(texts)

    class NeverExpander(_FakeQueryExpander):
        async def expand(self, query: str, *, model: str) -> ExpandedQueries:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(
        retrieval_service, "get_dense_embedder", lambda *args, **kwargs: SlowEmbedder()
    )
    result = await retrieve(
        session,
        ctx,
        ws.id,
        "alpha",
        query_expander=NeverExpander(),
        multi_query_expansion_timeout_ms_override=25,
    )

    assert cancelled.is_set()
    assert result.query_count == 1


async def test_multi_query_no_answer_uses_best_variant_dense_score(
    session: AsyncSession, qdrant_collection: None, utility_model: object
) -> None:
    ctx, ws = await seed_workspace(
        session,
        "mq-threshold",
        min_score=0.99,
        multi_query_enabled=True,
    )
    await upsert_texts(ctx, ws, ["flux capacitor requires 1.21 gigawatts"])
    expander = _FakeQueryExpander(
        ("flux capacitor requires 1.21 gigawatts",)
    )

    result = await retrieve(
        session, ctx, ws.id, "unrelated terminology", query_expander=expander
    )

    assert result.chunks
    assert result.no_answer is False


async def test_no_answer_rechecks_scores_after_high_candidate_is_removed(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.modules.documents import service as documents_service

    ctx, ws = await seed_workspace(
        session,
        "post-recheck-grounding",
        min_score=0.5,
        multi_query_enabled=False,
    )
    removed_id = uuid4()
    allowed_id = uuid4()
    probe_calls = 0

    class FakeQdrant:
        async def query_points(self, collection_name, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal probe_calls
            if kwargs.get("prefetch") is not None:
                return SimpleNamespace(
                    points=[
                        SimpleNamespace(
                            score=0.9,
                            payload={
                                "document_id": str(removed_id),
                                "page": 1,
                                "chunk_index": 0,
                                "text": "removed high evidence",
                            },
                        ),
                        SimpleNamespace(
                            score=0.1,
                            payload={
                                "document_id": str(allowed_id),
                                "page": 2,
                                "chunk_index": 0,
                                "text": "allowed weak evidence",
                            },
                        ),
                    ]
                )
            probe_calls += 1
            score = 0.99 if probe_calls == 1 else 0.1
            return SimpleNamespace(points=[SimpleNamespace(score=score)])

    checks = 0

    async def _unprojected(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal checks
        checks += 1
        return set() if checks == 1 else {removed_id}

    async def _ready(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(retrieval_service, "get_qdrant", lambda: FakeQdrant())
    monkeypatch.setattr(retrieval_service, "ensure_collection", _ready)
    monkeypatch.setattr(documents_service, "unprojected_document_ids", _unprojected)

    result = await retrieve(session, ctx, ws.id, "question", top_k=5)

    assert [chunk.document_id for chunk in result.chunks] == [allowed_id]
    assert result.no_answer is True
    assert probe_calls == 2


async def test_no_answer_probe_excludes_revision_mismatched_candidate(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.modules.documents import service as documents_service

    ctx, ws = await seed_workspace(
        session,
        "revision-recheck-grounding",
        min_score=0.5,
        multi_query_enabled=False,
    )
    removed_id = uuid4()
    allowed_id = uuid4()

    def _excluded_document_ids(query_filter: models.Filter) -> set[str]:
        result: set[str] = set()
        for condition in query_filter.must or []:
            if not isinstance(condition, models.Filter):
                continue
            for denied in condition.must_not or []:
                if (
                    isinstance(denied, models.FieldCondition)
                    and denied.key == "document_id"
                    and isinstance(denied.match, models.MatchAny)
                ):
                    result.update(str(value) for value in denied.match.any)
        return result

    class FakeQdrant:
        async def query_points(self, collection_name, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("prefetch") is not None:
                return SimpleNamespace(
                    points=[
                        SimpleNamespace(
                            score=0.9,
                            payload={
                                "document_id": str(removed_id),
                                "page": 1,
                                "chunk_index": 0,
                                "text": "removed high evidence",
                                "security_revision": 1,
                            },
                        ),
                        SimpleNamespace(
                            score=0.1,
                            payload={
                                "document_id": str(allowed_id),
                                "page": 2,
                                "chunk_index": 0,
                                "text": "allowed weak evidence",
                                "security_revision": 1,
                            },
                        ),
                    ]
                )
            excluded = _excluded_document_ids(kwargs["query_filter"])
            score = 0.1 if str(removed_id) in excluded else 0.99
            return SimpleNamespace(points=[SimpleNamespace(score=score)])

    async def _ready(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    async def _unprojected(*args, **kwargs):  # type: ignore[no-untyped-def]
        return set()

    async def _authorized(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {allowed_id: 1}

    monkeypatch.setattr(retrieval_service, "get_qdrant", lambda: FakeQdrant())
    monkeypatch.setattr(retrieval_service, "ensure_collection", _ready)
    monkeypatch.setattr(documents_service, "unprojected_document_ids", _unprojected)
    monkeypatch.setattr(documents_service, "authorized_security_revisions", _authorized)

    result = await retrieve(session, ctx, ws.id, "question", top_k=5)

    assert [chunk.document_id for chunk in result.chunks] == [allowed_id]
    assert result.no_answer is True


async def test_probe_only_document_cannot_supply_grounding_score(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.modules.documents import service as documents_service

    ctx, ws = await seed_workspace(
        session, "probe-only-grounding", min_score=0.5, multi_query_enabled=False
    )
    probe_only_id = uuid4()
    allowed_id = uuid4()

    class FakeQdrant:
        async def query_points(self, collection_name, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("prefetch") is not None:
                return SimpleNamespace(points=[SimpleNamespace(
                    score=0.1,
                    payload={"document_id": str(allowed_id), "page": 2,
                             "chunk_index": 0, "text": "allowed weak evidence",
                             "security_revision": 1},
                )])
            return SimpleNamespace(points=[SimpleNamespace(
                score=0.99,
                payload={"document_id": str(probe_only_id), "page": 1,
                         "chunk_index": 0, "security_revision": 1},
            )])

    async def ready(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    async def unprojected(*args, **kwargs):  # type: ignore[no-untyped-def]
        return set()

    async def authorized(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {allowed_id: 1}

    monkeypatch.setattr(retrieval_service, "get_qdrant", lambda: FakeQdrant())
    monkeypatch.setattr(retrieval_service, "ensure_collection", ready)
    monkeypatch.setattr(documents_service, "unprojected_document_ids", unprojected)
    monkeypatch.setattr(documents_service, "authorized_security_revisions", authorized)
    result = await retrieve(session, ctx, ws.id, "question", top_k=5)
    assert [chunk.document_id for chunk in result.chunks] == [allowed_id]
    assert result.no_answer is True


async def test_retrieve_uses_workspace_specific_collection(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """The whole point of DOC-10: a workspace on a non-seeded embedding model
    must be queried against ITS OWN collection, not the module's COLLECTION
    constant -- this is the exact bug the research for this plan found
    (retrieve() called ensure_collection(ws.embedding_model) but then
    hardcoded COLLECTION in both client.query_points calls). Pre-fix, this
    test fails outright: Workspace no longer even has an `embedding_model`
    attribute (Task 5 replaced it with `embedding_model_id`), so the old
    `await ensure_collection(ws.embedding_model)` line raises AttributeError
    before either query_points call is reached."""
    from ragz.modules.models import service as models_service

    ctx, ws = await seed_workspace(session, "orgh")
    # Seed the workspace's DEFAULT (bge-m3) collection with a matching chunk --
    # if retrieve() ever fell back to querying COLLECTION regardless of the
    # workspace's actual model, this text would be findable and the test below
    # would wrongly pass.
    await upsert_texts(ctx, ws, ["the flux capacitor requires 1.21 gigawatts"])

    other_model = await models_service.create_model(
        session, ctx,
        litellm_model_name="other-embed", display_name="Other",
        provider_kind="tei", base_url=None, api_key=None,
        settings=get_settings(), modality="embedding", dimension=1024,
    )
    ws.embedding_model_id = other_model.id
    await session.commit()

    # other_model's collection is brand new and empty -- a workspace pointed
    # at it must find NOTHING, even though a matching chunk exists in the
    # seeded default collection.
    result = await retrieve(session, ctx, ws.id, "flux capacitor gigawatts")
    assert result.chunks == []
    assert result.no_answer
    assert await get_qdrant().collection_exists(other_model.collection_name)
    assert other_model.collection_name != COLLECTION


async def test_multi_query_override_compares_without_mutating_workspace_setting(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
) -> None:
    ctx, ws = await seed_workspace(session, "mq-override", multi_query_enabled=False)
    expander = _FakeQueryExpander()

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "original",
        query_expander=expander,
        multi_query_enabled_override=True,
    )

    await session.refresh(ws)
    assert ws.multi_query_enabled is False
    assert expander.calls == [("original", "utility-model")]
    assert result.query_count == 3


async def test_five_query_override_uses_original_plus_four_alternatives(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
) -> None:
    ctx, ws = await seed_workspace(session, "mq-five", multi_query_enabled=False)
    expander = _FakeQueryExpander(("one", "two", "three", "four", "drop"))

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "original",
        query_expander=expander,
        multi_query_enabled_override=True,
        multi_query_count_override=5,
    )

    assert expander.calls == [("original", "utility-model")]
    assert result.query_count == 5


async def test_multi_query_count_override_rejects_unsupported_value(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    ctx, ws = await seed_workspace(session, "mq-count-invalid")
    with pytest.raises(ValueError, match="must be 1, 3, or 5"):
        await retrieve(
            session,
            ctx,
            ws.id,
            "original",
            multi_query_count_override=4,
        )


async def test_single_query_override_skips_expansion_on_enabled_workspace(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
) -> None:
    ctx, ws = await seed_workspace(session, "single-override", multi_query_enabled=True)
    expander = _FakeQueryExpander()

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "original",
        query_expander=expander,
        multi_query_enabled_override=False,
    )

    await session.refresh(ws)
    assert ws.multi_query_enabled is True
    assert expander.calls == []
    assert result.query_count == 1


async def test_single_query_count_override_skips_real_expander_on_enabled_workspace(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
) -> None:
    ctx, ws = await seed_workspace(
        session, "single-count-override", multi_query_enabled=True
    )

    result = await retrieve(
        session,
        ctx,
        ws.id,
        "original",
        multi_query_count_override=1,
    )

    assert result.query_count == 1


async def test_non_member_denied(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgd", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, ctx, ws.id, "anything")


async def test_admin_without_membership_allowed(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orge", role="admin", member=False)
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.chunks == []


async def test_delete_document_points(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgf")
    doc_id = await upsert_texts(ctx, ws, ["target text to delete"])
    from uuid import UUID
    await delete_document_points(ctx.org_id, UUID(doc_id), collection_name=COLLECTION)
    result = await retrieve(session, ctx, ws.id, "target text to delete")
    assert result.chunks == []


async def test_delete_restricted_document_points(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Verify deletion of a RESTRICTED document via delete_document_points:
    an unfiltered scroll must show zero points for that doc (maintenance posture
    ignores ACL). Kills the mutant hard-coding a groupset in delete's filter."""
    from uuid import UUID

    from ragz.modules.retrieval.client import COLLECTION

    ctx, ws = await seed_workspace(session, "orgg")
    # Seed a restricted document with ACL payload (acl_groups=[finance_group_id])
    document_id = str(uuid4())
    finance_id = uuid4()
    dense = await get_dense_embedder(**_LOCAL_MODEL_KW).embed(["restricted finance data"])
    sparse = await asyncio.to_thread(embed_sparse, ["restricted finance data"])
    points = [
        models.PointStruct(
            id=str(uuid4()),
            vector={"dense": dense[0], "sparse": sparse[0]},
            payload={
                "tenant_id": str(ctx.org_id),
                "workspace_id": str(ws.id),
                "document_id": document_id,
                "page": 1,
                "chunk_index": 0,
                "text": "restricted finance data",
                "doc_type": "text/plain",
                "date": "2026-07-18",
                "acl_groups": [str(finance_id)],  # non-empty ACL payload
            },
        )
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)

    # Delete the document via delete_document_points (maintenance path)
    await delete_document_points(ctx.org_id, UUID(document_id), collection_name=COLLECTION)

    # Unfiltered scroll must show zero points for that document
    all_points, _ = await get_qdrant().scroll(COLLECTION, limit=100)
    doc_points = [p for p in all_points if (p.payload or {}).get("document_id") == document_id]
    assert doc_points == []


async def test_delete_points_tolerates_missing_collection(stack_env: None) -> None:
    """Deleting a never-indexed document must be a no-op, not a Qdrant 404 (smoke regression).

    Uses stack_env (NOT the bare qdrant_url fixture) so get_qdrant() is redirected to
    the test container — otherwise this test's delete_collection would hit whatever
    Qdrant ambient settings point at (it once wiped the dev compose collection).
    """
    from uuid import uuid4

    from ragz.modules.retrieval.client import COLLECTION, get_qdrant
    from ragz.modules.retrieval.service import delete_document_points

    client = get_qdrant()
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    await delete_document_points(uuid4(), uuid4(), collection_name=COLLECTION)  # must not raise


def test_dedupe_hq_keeps_max_score_per_chunk_ref() -> None:
    doc_id = uuid4()
    parent = RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="t", score=0.4)
    hq_hit = RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="t", score=0.9)
    other = RetrievedChunk(document_id=doc_id, page=1, chunk_index=1, text="u", score=0.5)
    result = _dedupe_hq([parent, hq_hit, other])
    assert len(result) == 2
    kept = next(r for r in result if r.chunk_index == 0)
    assert kept.score == 0.9


async def test_ensure_collection_heal_branch_runs_once_per_process(
    qdrant_collection: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """qdrant_collection already ensures the collection exists (via a prior
    ensure_collection() call in the fixture), so the calls made here run the
    heal (else) branch. _HEALED is module-level and persists across the test
    process, so it's reset here for a deterministic first-touch state."""
    monkeypatch.setattr(retrieval_service, "_HEALED", set())
    client = get_qdrant()
    calls: list[int] = []
    original = client.create_payload_index

    async def counting(*a, **kw):  # type: ignore[no-untyped-def]
        calls.append(1)
        return await original(*a, **kw)

    monkeypatch.setattr(client, "create_payload_index", counting)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    assert len(calls) == 2  # exactly the first call's 2 heal indexes, never repeated


def test_dedupe_hq_noop_when_no_duplicates() -> None:
    doc_id = uuid4()
    chunks = [
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="a", score=0.5),
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=1, text="b", score=0.3),
    ]
    assert _dedupe_hq(chunks) == chunks
