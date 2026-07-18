import httpx
import pytest

from raghub.modules.retrieval.rerank import (
    LexicalReranker,
    RerankUnavailable,
    TeiReranker,
    get_reranker,
)


def _tei_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/rerank"
    import json

    body = json.loads(request.content)
    assert body["raw_scores"] is False
    # TEI returns results sorted by score, carrying the original index.
    n = len(body["texts"])
    results = [{"index": i, "score": (n - i) / n} for i in reversed(range(n))]
    return httpx.Response(200, json=results)


async def test_tei_reranker_realigns_scores_by_index() -> None:
    r = TeiReranker("http://tei-rerank", transport=httpx.MockTransport(_tei_handler))
    scores = await r.rerank("q", ["a", "b", "c"])
    assert scores == [3 / 3, 2 / 3, 1 / 3]  # positional, regardless of TEI's sort order


async def test_tei_reranker_unavailable_raises_typed_error() -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    r = TeiReranker("http://tei-rerank", transport=httpx.MockTransport(down))
    with pytest.raises(RerankUnavailable):
        await r.rerank("q", ["a"])


async def test_tei_reranker_5xx_raises_typed_error() -> None:
    r = TeiReranker(
        "http://tei-rerank",
        transport=httpx.MockTransport(lambda _: httpx.Response(503, text="overloaded")),
    )
    with pytest.raises(RerankUnavailable):
        await r.rerank("q", ["a"])


async def test_lexical_reranker_is_deterministic_overlap() -> None:
    r = LexicalReranker()
    scores = await r.rerank(
        "launch checklist", ["the launch checklist steps", "quarterly budget", ""]
    )
    assert scores == [1.0, 0.0, 0.0]
    assert await r.rerank("", ["anything"]) == [0.0]


async def test_get_reranker_selects_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from raghub.core.config import get_settings

    monkeypatch.setenv("RAGHUB_RERANK_BACKEND", "lexical")
    get_settings.cache_clear()
    get_reranker.cache_clear()
    assert isinstance(get_reranker(), LexicalReranker)
    get_settings.cache_clear()
    get_reranker.cache_clear()
