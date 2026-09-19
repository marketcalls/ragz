from pathlib import Path

import httpx
import pytest

from ragz.core.app_settings import set_app_setting
from ragz.core.config import Settings
from ragz.modules.retrieval.rerank import (
    CohereReranker,
    LexicalReranker,
    RerankMisconfigured,
    RerankUnavailable,
    TeiReranker,
    get_reranker,
)
from ragz.modules.secrets import service as secrets_service
from ragz.modules.secrets.crypto import ensure_kek


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


def _cohere_transport(payload: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/rerank"
        assert request.headers["authorization"] == "Bearer ck-test"
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


async def test_cohere_reranker_aligns_scores_to_input_order() -> None:
    # Cohere returns results sorted by score; we must realign to input order.
    payload = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.4},
            {"index": 1, "relevance_score": 0.1},
        ]
    }
    r = CohereReranker(
        base_url="https://api.cohere.com",
        api_key="ck-test",
        model="rerank-v4.0-fast",
        transport=_cohere_transport(payload),
    )
    scores = await r.rerank("q", ["a", "b", "c"])
    assert scores == [0.4, 0.1, 0.9]


async def test_cohere_reranker_captures_billed_search_units() -> None:
    # Cost reporting (design 2026-08-15): meta.billed_units.search_units is
    # surfaced on the instance for the retrieval call site to record.
    payload = {
        "results": [
            {"index": 0, "relevance_score": 0.7},
            {"index": 1, "relevance_score": 0.3},
        ],
        "meta": {"billed_units": {"search_units": 3}},
    }
    r = CohereReranker(
        base_url="https://api.cohere.com", api_key="ck-test",
        model="rerank-v4.0-fast", transport=_cohere_transport(payload),
    )
    await r.rerank("q", ["a", "b"])
    assert r.last_search_units == 3


async def test_cohere_reranker_search_units_defaults_to_one_when_absent() -> None:
    # A response without meta.billed_units still bills at least one unit -- a
    # performed call under-reporting to 0 would silently hide real cost.
    payload = {"results": [{"index": 0, "relevance_score": 0.9}]}
    r = CohereReranker(
        base_url="https://api.cohere.com", api_key="ck-test",
        model="rerank-v4.0-fast", transport=_cohere_transport(payload),
    )
    await r.rerank("q", ["a"])
    assert r.last_search_units == 1


async def test_cohere_reranker_maps_http_error_to_rerank_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api token"})

    r = CohereReranker(
        base_url="https://api.cohere.com", api_key="bad", model="rerank-v4.0-fast",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RerankUnavailable, match="HTTP 401"):
        await r.rerank("q", ["a", "b"])


async def test_cohere_retries_429_honors_retry_after_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(
            200,
            json={
                "results": [{"index": 0, "relevance_score": 0.8}],
                "meta": {"billed_units": {"search_units": 1}},
            },
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    reranker = CohereReranker(
        base_url="https://api.cohere.com",
        api_key="ck-test",
        model="rerank-v4.0-fast",
        transport=httpx.MockTransport(handler),
        max_retries=2,
        base_backoff_seconds=0.25,
        sleep=sleep,
    )

    assert await reranker.rerank("q", ["a"]) == [0.8]
    assert attempts == 2
    assert delays == [2.0]
    assert reranker.last_attempts == 2
    assert reranker.last_retry_wait_ms == 2_000


async def test_cohere_exhausts_retryable_503_with_bounded_backoff() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    reranker = CohereReranker(
        base_url="https://api.cohere.com",
        api_key="ck-test",
        model="rerank-v4.0-fast",
        transport=httpx.MockTransport(handler),
        max_retries=2,
        base_backoff_seconds=0.25,
        sleep=sleep,
    )

    with pytest.raises(RerankUnavailable, match="HTTP 503"):
        await reranker.rerank("q", ["a"])
    assert attempts == 3
    assert delays == [0.25, 0.5]
    assert reranker.last_attempts == 3


async def test_cohere_does_not_retry_non_retryable_401() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401)

    reranker = CohereReranker(
        base_url="https://api.cohere.com",
        api_key="bad",
        model="rerank-v4.0-fast",
        transport=httpx.MockTransport(handler),
        max_retries=3,
    )

    with pytest.raises(RerankUnavailable, match="HTTP 401"):
        await reranker.rerank("q", ["a"])
    assert attempts == 1
    assert reranker.last_attempts == 1


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        ("not-a-number", 0.25),
        ("999", 30.0),
        ("-3", 0.25),
        ("Wed, 01 Jan 2099 00:00:00 GMT", 30.0),
        ("Sat, 01 Jan 2000 00:00:00 GMT", 0.0),
    ],
)
async def test_cohere_retry_after_is_validated_and_bounded(
    retry_after: str, expected_delay: float
) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    reranker = CohereReranker(
        base_url="https://api.cohere.com",
        api_key="ck-test",
        model="rerank-v4.0-fast",
        transport=httpx.MockTransport(handler),
        max_retries=1,
        base_backoff_seconds=0.25,
        sleep=sleep,
    )

    assert await reranker.rerank("q", ["a"]) == [0.5]
    assert delays == [expected_delay]


async def test_resolver_defaults_to_local_tei(pristine_env, session, settings) -> None:
    # No app_setting set -> local path. settings.rerank_backend default "tei".
    r = await get_reranker(session, settings)
    assert isinstance(r, TeiReranker)


async def test_resolver_local_lexical_when_env_lexical(session) -> None:
    s = Settings(_env_file=None, rerank_backend="lexical")
    r = await get_reranker(session, s)
    assert isinstance(r, LexicalReranker)


async def test_resolver_cohere_returns_cohere_reranker(session, settings) -> None:
    await set_app_setting(session, "rerank_provider", "cohere")
    await secrets_service.set_secret(
        session, actor_id=None, name="cohere_api_key", value="ck-live", settings=settings
    )
    r = await get_reranker(session, settings)
    assert isinstance(r, CohereReranker)
    assert r._max_retries == settings.cohere_rerank_max_retries  # noqa: SLF001
    assert (  # noqa: SLF001
        r._base_backoff_seconds == settings.cohere_rerank_base_backoff_seconds
    )


async def test_resolver_cohere_without_key_raises_misconfigured(session, settings) -> None:
    await set_app_setting(session, "rerank_provider", "cohere")
    with pytest.raises(RerankMisconfigured):
        await get_reranker(session, settings)
