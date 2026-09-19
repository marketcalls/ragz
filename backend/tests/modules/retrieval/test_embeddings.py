import asyncio
import json
import math
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from prometheus_client import REGISTRY

from ragz.core.config import Settings
from ragz.core.errors import UpstreamError
from ragz.modules.retrieval.embeddings import (
    HashDenseEmbedder,
    InMemoryQueryEmbeddingCache,
    LiteLLMEmbedder,
    TeiDenseEmbedder,
    embed_sparse,
    get_dense_embedder,
    get_query_embedding_cache,
    query_embedding_cache_namespace,
)


@dataclass
class _Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hash_embedder_deterministic_and_normalized() -> None:
    emb = HashDenseEmbedder(dim=64)
    [v1] = await emb.embed(["the flux capacitor hums"])
    [v2] = await emb.embed(["the flux capacitor hums"])
    assert v1 == v2 and len(v1) == 64
    assert math.isclose(sum(x * x for x in v1), 1.0, rel_tol=1e-6)


async def test_hash_embedder_overlap_beats_disjoint() -> None:
    emb = HashDenseEmbedder(dim=256)
    [q, hit, miss] = await emb.embed(
        [
            "flux capacitor invoice",
            "invoice 0231 for the flux capacitor",
            "quarterly kumquat report",
        ]
    )
    assert _cos(q, hit) > _cos(q, miss)


async def test_query_embedding_cache_is_exact_bounded_and_copy_safe() -> None:
    cache = InMemoryQueryEmbeddingCache(max_entries=2)
    namespace = "model-space"

    assert await cache.get_many(namespace, ["alpha", "beta"]) == [None, None]
    await cache.set_many(namespace, ["alpha", "beta"], [[1.0, 2.0], [3.0, 4.0]])
    first = await cache.get_many(namespace, ["alpha", "beta"])
    assert first == [[1.0, 2.0], [3.0, 4.0]]
    assert first[0] is not None
    first[0][0] = 99.0
    assert await cache.get_many(namespace, ["alpha"]) == [[1.0, 2.0]]

    await cache.set_many(namespace, ["gamma"], [[5.0, 6.0]])
    assert await cache.get_many(namespace, ["beta", "alpha", "gamma"]) == [
        None,
        [1.0, 2.0],
        [5.0, 6.0],
    ]


async def test_query_embedding_cache_coalesces_concurrent_cold_computes() -> None:
    cache = InMemoryQueryEmbeddingCache(max_entries=2)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def compute(texts: list[str]) -> tuple[list[list[float]], int]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [[1.0, 2.0] for _ in texts], 7

    first = asyncio.create_task(cache.get_or_compute("space", ["alpha"], compute))
    await started.wait()
    second = asyncio.create_task(cache.get_or_compute("space", ["alpha"], compute))
    await asyncio.sleep(0)

    assert calls == 1
    release.set()
    owner, waiter = await asyncio.gather(first, second)

    assert owner[0] == waiter[0] == [[1.0, 2.0]]
    assert owner[1] == 7
    assert waiter[1] == 0
    assert owner[2:4] == (0, 1)
    assert waiter[2:4] == (0, 1)
    assert owner[4] >= 0.0  # lookup
    assert owner[5] >= 0.0  # store
    assert owner[6] == 0.0  # owner never waits on another request
    assert waiter[6] >= 0.0


async def test_query_embedding_singleflight_failure_fans_out_and_recovers() -> None:
    cache = InMemoryQueryEmbeddingCache(max_entries=2)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def failing(_texts: list[str]) -> tuple[list[list[float]], int]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        raise UpstreamError("provider down")

    owner = asyncio.create_task(cache.get_or_compute("space", ["alpha"], failing))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_compute("space", ["alpha"], failing))
    await asyncio.sleep(0)
    release.set()
    failed = await asyncio.gather(owner, waiter, return_exceptions=True)

    assert calls == 1
    assert all(isinstance(item, UpstreamError) for item in failed)

    async def recovered(_texts: list[str]) -> tuple[list[list[float]], int]:
        nonlocal calls
        calls += 1
        return [[3.0, 4.0]], 5

    result = await cache.get_or_compute("space", ["alpha"], recovered)
    assert result[0] == [[3.0, 4.0]]
    assert result[1] == 5
    assert calls == 2


async def test_query_embedding_waiter_retries_after_owner_cancellation() -> None:
    cache = InMemoryQueryEmbeddingCache(max_entries=2)
    started = asyncio.Event()
    calls = 0

    async def compute(_texts: list[str]) -> tuple[list[list[float]], int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()
        return [[5.0, 6.0]], 9

    owner = asyncio.create_task(cache.get_or_compute("space", ["alpha"], compute))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_compute("space", ["alpha"], compute))
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    result = await asyncio.wait_for(waiter, timeout=1)
    assert result[0] == [[5.0, 6.0]]
    assert result[1] == 9
    assert calls == 2


async def test_query_embedding_publication_cancellation_resolves_all_keys_and_waiters() -> None:
    cache = InMemoryQueryEmbeddingCache(max_entries=8)
    compute_started = asyncio.Event()
    release_compute = asyncio.Event()
    calls: list[tuple[str, ...]] = []

    async def compute(texts: list[str]) -> tuple[list[list[float]], int]:
        calls.append(tuple(texts))
        if "alpha" in texts:
            compute_started.set()
            await release_compute.wait()
        return [[float(index + 1)] for index, _ in enumerate(texts)], 7

    owner = asyncio.create_task(
        cache.get_or_compute("space", ["alpha", "gamma"], compute)
    )
    await compute_started.wait()
    waiter = asyncio.create_task(
        cache.get_or_compute("space", ["alpha", "gamma"], compute)
    )
    unrelated = await cache.get_or_compute("space", ["beta"], compute)
    assert unrelated[0] == [[1.0]]

    await cache._lock.acquire()  # noqa: SLF001 - deterministic vulnerable await boundary
    release_compute.set()
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    cache._lock.release()  # noqa: SLF001

    recovered = await asyncio.wait_for(waiter, timeout=1)
    assert recovered[0] == [[1.0], [2.0]]
    assert calls.count(("alpha", "gamma")) == 1
    cached = await cache.get_or_compute("space", ["alpha", "gamma"], compute)
    assert cached[0] == [[1.0], [2.0]]
    assert cached[1] == 0


async def test_query_embedding_cache_rejects_misaligned_batch() -> None:
    cache = InMemoryQueryEmbeddingCache()
    with pytest.raises(ValueError, match="same length"):
        await cache.set_many("space", ["one"], [])


async def test_query_embedding_cache_expires_entries_after_ttl() -> None:
    clock = _Clock()
    cache = InMemoryQueryEmbeddingCache(
        max_entries=2, ttl_seconds=10.0, clock=clock
    )
    await cache.set_many("space", ["one"], [[1.0, 2.0]])

    clock.value = 9.999
    assert await cache.get_many("space", ["one"]) == [[1.0, 2.0]]
    clock.value = 10.0
    assert await cache.get_many("space", ["one"]) == [None]


def test_query_embedding_cache_resolver_is_bounded_shared_and_disableable() -> None:
    disabled = Settings(_env_file=None, query_embedding_cache_enabled=False)
    enabled = Settings(
        _env_file=None,
        query_embedding_cache_enabled=True,
        query_embedding_cache_max_entries=7,
        query_embedding_cache_ttl_seconds=11,
    )

    assert get_query_embedding_cache(disabled) is None
    first = get_query_embedding_cache(enabled)
    second = get_query_embedding_cache(enabled)
    assert first is second


async def test_query_embedding_cache_metrics_use_only_bounded_outcomes() -> None:
    def count(outcome: str) -> float:
        return REGISTRY.get_sample_value(
            "ragz_query_embedding_cache_operations_total", {"outcome": outcome}
        ) or 0.0

    before = {outcome: count(outcome) for outcome in ("hit", "miss", "store")}
    cache = InMemoryQueryEmbeddingCache(max_entries=2, ttl_seconds=10)

    assert await cache.get_many("space", ["alpha"]) == [None]
    await cache.set_many("space", ["alpha"], [[1.0]])
    assert await cache.get_many("space", ["alpha"]) == [[1.0]]

    assert count("miss") == before["miss"] + 1
    assert count("store") == before["store"] + 1
    assert count("hit") == before["hit"] + 1


def test_query_embedding_cache_namespace_binds_tenant_model_and_dimension() -> None:
    org_id = uuid4()
    model_id = uuid4()
    base = query_embedding_cache_namespace(
        org_id=org_id,
        model_id=model_id,
        provider_kind="openai",
        model="text-embedding-3-large",
        dimension=1024,
    )
    changed = query_embedding_cache_namespace(
        org_id=org_id,
        model_id=model_id,
        provider_kind="openai",
        model="text-embedding-3-large",
        dimension=1536,
    )
    other_org = query_embedding_cache_namespace(
        org_id=uuid4(),
        model_id=model_id,
        provider_kind="openai",
        model="text-embedding-3-large",
        dimension=1024,
    )
    assert len(base) == 64
    assert base != changed
    assert base != other_org


async def test_tei_embedder_batches_and_parses() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["inputs"]
        calls.append(inputs)
        return httpx.Response(200, json=[[0.1, 0.2]] * len(inputs))

    emb = TeiDenseEmbedder("http://tei", batch_size=2, transport=httpx.MockTransport(handler))
    vecs = await emb.embed(["a", "b", "c"])
    assert vecs == [[0.1, 0.2]] * 3
    assert [len(c) for c in calls] == [2, 1]  # batched


def test_sparse_bm25_hits_shared_terms() -> None:
    [doc, query] = embed_sparse(["invoice 0231 total due", "invoice 0231"])
    assert set(query.indices) & set(doc.indices)  # shared term indices
    assert all(v > 0 for v in doc.values)


async def test_litellm_embedder_posts_and_parses_embeddings() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read()
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2, 0.3]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", transport=transport,
    )
    result = await embedder.embed(["hello", "world"])
    assert result == [[0.1, 0.2], [0.2, 0.3]]  # re-ordered by index, not response order
    assert str(captured["url"]).endswith("/v1/embeddings")


async def test_litellm_openai_embedder_requests_dimensions() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
            },
        )

    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", provider_kind="openai", dimension=3,
        transport=httpx.MockTransport(handler),
    )
    vectors = await embedder.embed(["hello"])

    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured["json"] == {
        "model": "text-embedding-3-small", "input": ["hello"], "dimensions": 3
    }


async def test_litellm_embedder_rejects_requested_width_mismatch() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )
    )
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", provider_kind="openai", dimension=3,
        transport=transport,
    )

    with pytest.raises(UpstreamError, match="expected 3 dimensions"):
        await embedder.embed(["hello"])


@pytest.mark.parametrize(
    ("provider_kind", "model"),
    [
        ("cohere", "embed-english-v3.0"),
        ("cohere", "text-embedding-3-small"),
        ("openai", "text-embedding-ada-002"),
    ],
)
async def test_litellm_embedder_omits_dimensions_for_unsupported_models(
    provider_kind: str, model: str,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )

    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master", model=model,
        provider_kind=provider_kind, dimension=2, transport=httpx.MockTransport(handler),
    )
    await embedder.embed(["hello"])

    assert "dimensions" not in captured["json"]


async def test_litellm_embedder_reports_billed_tokens() -> None:
    # Cost reporting (design 2026-08-15): embed_with_usage surfaces the hosted
    # provider's usage.total_tokens, summed across batches, for the recording
    # call site. Same vectors as embed().
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["input"])
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": [0.1, 0.2]} for i in range(n)],
            "usage": {"total_tokens": 11 * n},
        })

    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", batch_size=2,
        transport=httpx.MockTransport(handler),
    )
    vecs, tokens = await embedder.embed_with_usage(["a", "b", "c"])
    assert vecs == [[0.1, 0.2]] * 3
    assert tokens == 11 * 3  # 22 (batch of 2) + 11 (batch of 1)


async def test_litellm_embedder_missing_usage_degrades_to_zero_tokens() -> None:
    # A response without a usage block must not fail -- undercount to 0.
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})
    )
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", transport=transport,
    )
    _vecs, tokens = await embedder.embed_with_usage(["hello"])
    assert tokens == 0


async def test_self_hosted_embedders_report_zero_tokens() -> None:
    # TEI and the hash backend are free -> 0 billed tokens.
    tei = TeiDenseEmbedder(
        "http://tei",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[[0.1, 0.2]])),
    )
    _v, tei_tokens = await tei.embed_with_usage(["a"])
    assert tei_tokens == 0
    _v2, hash_tokens = await HashDenseEmbedder(dim=8).embed_with_usage(["a"])
    assert hash_tokens == 0


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503, text="unavailable"), httpx.Response(200, content=b"not-json")],
)
async def test_tei_provider_failures_are_normalized_for_q1_fallback(
    response: httpx.Response,
) -> None:
    embedder = TeiDenseEmbedder(
        "http://tei.test", transport=httpx.MockTransport(lambda _request: response)
    )
    with pytest.raises(UpstreamError):
        await embedder.embed(["alternative"])


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"data": [{"embedding": [0.1]}]},
        {"data": [{"index": 0}]},
        {"data": "bad"},
        {"data": [{"index": 0, "embedding": ["not-a-number"]}]},
    ],
)
async def test_litellm_response_shape_failures_are_normalized_for_q1_fallback(
    body: object,
) -> None:
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body)),
    )
    with pytest.raises(UpstreamError, match="malformed embedding response"):
        await embedder.embed(["alternative"])


async def test_litellm_nonfinite_embedding_is_normalized() -> None:
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b'{"data":[{"index":0,"embedding":[NaN]}]}',
                headers={"content-type": "application/json"},
            )
        ),
    )
    with pytest.raises(UpstreamError, match="malformed embedding response"):
        await embedder.embed(["alternative"])


async def test_real_litellm_width_failure_preserves_reported_usage() -> None:
    from ragz.modules.retrieval.service import _embed_query_batch

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"total_tokens": 17},
            },
        )
    )
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", dimension=3, transport=transport,
    )
    recorded: list[int] = []

    async def record(tokens: int) -> None:
        recorded.append(tokens)

    with pytest.raises(UpstreamError, match="expected 3 dimensions"):
        await _embed_query_batch(
            queries=("alternative",), dense_embedder=embedder, query_cache=None,
            cache_namespace="org-model", expected_dimension=3,
            stage_timings_ms=None, record_billed_usage=record,
        )
    assert recorded == [17]


async def test_real_litellm_later_batch_failure_preserves_earlier_usage() -> None:
    from ragz.modules.retrieval.service import _embed_query_batch

    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                    "usage": {"total_tokens": 5},
                },
            )
        return httpx.Response(503, text="unavailable")

    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", dimension=2,
        transport=httpx.MockTransport(handler), batch_size=1,
    )
    recorded: list[int] = []

    async def record(tokens: int) -> None:
        recorded.append(tokens)

    with pytest.raises(UpstreamError, match="HTTP 503"):
        await _embed_query_batch(
            queries=("one", "two"), dense_embedder=embedder, query_cache=None,
            cache_namespace="org-model", expected_dimension=2,
            stage_timings_ms=None, record_billed_usage=record,
        )
    assert recorded == [5]


@pytest.mark.parametrize("usage", ["bad", [1]])
async def test_litellm_malformed_usage_is_normalized(usage: object) -> None:
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.1]}], "usage": usage},
            )
        ),
    )
    with pytest.raises(UpstreamError, match="malformed embedding response"):
        await embedder.embed_with_usage(["alternative"])


async def test_litellm_embedder_non_200_raises_upstream_error() -> None:
    from ragz.core.errors import UpstreamError

    marker = "provider-secret-response-body"
    transport = httpx.MockTransport(lambda r: httpx.Response(500, text=marker))
    embedder = LiteLLMEmbedder(
        base_url="http://litellm.test", master_key="sk-master",
        model="text-embedding-3-small", transport=transport,
    )
    with pytest.raises(UpstreamError) as exc_info:
        await embedder.embed(["hello"])
    assert marker not in str(exc_info.value)


def test_get_dense_embedder_routes_tei_vs_litellm(pristine_env) -> None:
    tei_id = uuid4()
    other_id = uuid4()
    tei = get_dense_embedder(tei_id, provider_kind="tei", litellm_model_name="local-embeddings")
    other = get_dense_embedder(
        other_id, provider_kind="openai", litellm_model_name="text-embedding-3-small"
    )
    assert isinstance(tei, TeiDenseEmbedder)
    assert isinstance(other, LiteLLMEmbedder)


def test_get_dense_embedder_caches_by_model_id() -> None:
    model_id = uuid4()
    a = get_dense_embedder(model_id, provider_kind="tei", litellm_model_name="local-embeddings")
    b = get_dense_embedder(model_id, provider_kind="tei", litellm_model_name="local-embeddings")
    assert a is b


def test_get_dense_embedder_cache_separates_dimensions() -> None:
    model_id = uuid4()
    a = get_dense_embedder(
        model_id, provider_kind="tei", litellm_model_name="local-embeddings", dimension=1024
    )
    b = get_dense_embedder(
        model_id, provider_kind="tei", litellm_model_name="local-embeddings", dimension=1536
    )
    assert a is not b


async def test_an_unreachable_tei_names_the_service_and_the_fix() -> None:
    """Issue #1: this is the most likely first-run failure.

    A fresh workspace selects the built-in local model, but TEI sits behind the
    local-embeddings Compose profile and so is simply not running. httpx's own
    message -- "All connection attempts failed" -- names neither the service,
    nor the URL, nor what to do, which is what the reporter actually saw after
    parse and chunk had already succeeded.
    """
    def _refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed")

    emb = TeiDenseEmbedder(
        "http://localhost:58080", transport=httpx.MockTransport(_refuse)
    )

    with pytest.raises(UpstreamError) as excinfo:
        await emb.embed(["anything"])

    message = str(excinfo.value)
    assert "localhost:58080" in message, "must name the endpoint that failed"
    assert "local-embeddings" in message, "must name the profile that starts it"
    assert "Admin" in message, "must offer the hosted alternative"
    assert "All connection attempts failed" not in message
