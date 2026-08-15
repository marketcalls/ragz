"""Cross-encoder reranking client (PRD CHAT-2 pull-forward, Plan E).

The Reranker Protocol is the test seam; TeiReranker is the only HTTP client
(mocked at the httpx layer — the one sanctioned mock). LexicalReranker is the
deterministic dev/test backend, playing the same role HashDenseEmbedder plays
for dense embeddings: "relevance" reduces to lexical overlap, so tests are
scoped accordingly and true ranking quality is validated in the live smoke.

Iron rule 3 note: sanctioned caller of secrets._get_secret_decrypted — the
Cohere API key is decrypted in memory for exactly one outbound rerank call
and never returned, logged, or persisted. Named in the allowlist test
(tests/modules/models/test_sync.py).
"""

import re
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.config import Settings
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.secrets import service as secrets_service

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class RerankUnavailable(Exception):
    """Reranker unreachable/failed — callers degrade to fusion order (NFR)."""


class Reranker(Protocol):
    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Relevance scores in [0, 1], positionally aligned with `texts`."""
        ...


class TeiReranker:
    """TEI /rerank (BAAI/bge-reranker-v2-m3). raw_scores=false → sigmoid scores
    in [0, 1], which is what workspace.min_score is compared against when
    rerank_enabled (see retrieve())."""

    def __init__(
        self, base_url: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = base_url
        self._transport = transport

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=30.0, transport=self._transport
            ) as client:
                r = await client.post(
                    "/rerank",
                    json={
                        "query": query,
                        "texts": texts,
                        "raw_scores": False,
                        "truncate": True,
                    },
                )
                r.raise_for_status()
            scores = [0.0] * len(texts)
            for item in r.json():  # [{"index": i, "score": s}, ...] sorted by score
                idx = int(item["index"])
                if idx < 0 or idx >= len(texts):
                    raise IndexError(f"reranker returned out-of-bounds index {idx}")
                scores[idx] = float(item["score"])
            return scores
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise RerankUnavailable("reranker returned an unusable response") from exc


class LexicalReranker:
    """Deterministic stand-in: fraction of query tokens present in the text."""

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        q = set(_TOKEN_RE.findall(query.lower()))
        if not q:
            return [0.0] * len(texts)
        return [
            len(q & set(_TOKEN_RE.findall(t.lower()))) / len(q) for t in texts
        ]


class RerankMisconfigured(ConflictError):
    """The Cohere reranker is selected but no API key is configured. Unlike
    RerankUnavailable (a transient outage that degrades to fusion order), this
    is an operator misconfiguration and must surface a clear 409, not silently
    fall back."""


COHERE_RERANK_MODELS = ("rerank-v4.0-fast", "rerank-v4.0-pro")
COHERE_RERANK_DEFAULT = "rerank-v4.0-fast"


def _search_units(body: dict[str, object]) -> int:
    """Billed search-units from a Cohere v2 rerank response
    (meta.billed_units.search_units). Any absence/malformation falls back to 1:
    a performed rerank call bills at least one unit, so under-reporting to 0
    would silently hide real cost."""
    meta = body.get("meta")
    billed = meta.get("billed_units") if isinstance(meta, dict) else None
    raw = billed.get("search_units") if isinstance(billed, dict) else None
    try:
        return int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        return 1


class CohereReranker:
    """Cohere Rerank v4 API (rerank-v4.0-fast | rerank-v4.0-pro). Same
    [0,1]-scores-aligned-to-input contract as TeiReranker so retrieve() is
    backend-agnostic."""

    def __init__(
        self, *, base_url: str, api_key: str, model: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._transport = transport
        # Cost reporting (design 2026-08-15): Cohere bills in "search units".
        # Set from the response's meta.billed_units on each call; the retrieval
        # call site reads it to record feature="rerank" usage. Instance state is
        # safe here (unlike the lru_cached embedder) -- get_reranker builds a
        # fresh CohereReranker per retrieve() call, so it is never shared.
        self.last_search_units: int = 0

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=30.0, transport=self._transport
            ) as client:
                r = await client.post(
                    "/v2/rerank",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "query": query,
                        "documents": texts,
                        "top_n": len(texts),
                    },
                )
                r.raise_for_status()
            body = r.json()
            scores = [0.0] * len(texts)
            for item in body["results"]:  # sorted by score; realign to input
                idx = int(item["index"])
                if idx < 0 or idx >= len(texts):
                    raise IndexError(f"cohere returned out-of-bounds index {idx}")
                scores[idx] = float(item["relevance_score"])
            # meta.billed_units.search_units is the billed count; a v2 response
            # that omits it (or ships a non-int) falls back to 1 -- one call
            # billed at least one unit, never zero.
            self.last_search_units = _search_units(body)
            return scores
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise RerankUnavailable("cohere reranker returned an unusable response") from exc


async def get_reranker(session: AsyncSession, settings: Settings) -> Reranker:
    """Resolve the active reranker. `rerank_provider` app_setting picks the
    backend; `local` (default) preserves today's TEI/lexical behavior exactly,
    `cohere` uses the encrypted key. Not cached: it must reflect live setting
    changes, and instantiation is cheap."""
    provider = await get_app_setting(session, "rerank_provider")
    if provider == "cohere":
        try:
            key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
                session, name="cohere_api_key", settings=settings
            )
        except NotFoundError as exc:
            raise RerankMisconfigured(
                "Cohere reranker selected but no API key is configured"
            ) from exc
        model = await get_app_setting(session, "cohere_rerank_model") or COHERE_RERANK_DEFAULT
        return CohereReranker(base_url="https://api.cohere.com", api_key=key, model=model)
    if settings.rerank_backend == "lexical":
        return LexicalReranker()
    return TeiReranker(settings.rerank_url)
