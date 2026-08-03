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


async def test_cohere_reranker_maps_http_error_to_rerank_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api token"})

    r = CohereReranker(
        base_url="https://api.cohere.com", api_key="bad", model="rerank-v4.0-fast",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RerankUnavailable):
        await r.rerank("q", ["a", "b"])


async def test_resolver_defaults_to_local_tei(session, settings) -> None:
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


async def test_resolver_cohere_without_key_raises_misconfigured(session, settings) -> None:
    await set_app_setting(session, "rerank_provider", "cohere")
    with pytest.raises(RerankMisconfigured):
        await get_reranker(session, settings)
