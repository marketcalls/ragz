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

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.config import Settings
from ragz.core.errors import ConflictError, NotFoundError
from ragz.core.metrics import (
    rerank_provider_attempts_total,
    rerank_provider_duration_seconds,
    rerank_retry_wait_seconds,
)
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
_COHERE_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


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
        max_retries: int = 2,
        base_backoff_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        if base_backoff_seconds < 0:
            raise ValueError("base_backoff_seconds must be non-negative")
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._transport = transport
        self._max_retries: int = max_retries
        self._base_backoff_seconds: float = float(base_backoff_seconds)
        self._sleep = sleep
        # Cost reporting (design 2026-08-15): Cohere bills in "search units".
        # Set from the response's meta.billed_units on each call; the retrieval
        # call site reads it to record feature="rerank" usage. Instance state is
        # safe here (unlike the lru_cached embedder) -- get_reranker builds a
        # fresh CohereReranker per retrieve() call, so it is never shared.
        self.last_search_units: int = 0
        self.last_attempts: int = 0
        self.last_retry_wait_ms: float = 0.0
        self.last_provider_latency_ms: float = 0.0
        self.last_local_latency_ms: float = 0.0

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            parsed_http_date = False
            try:
                parsed = float(raw) if raw is not None else -1.0
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(raw or "")
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    parsed = (retry_at - datetime.now(UTC)).total_seconds()
                    parsed_http_date = True
                except (TypeError, ValueError, OverflowError):
                    parsed = -1.0
            if parsed_http_date and math.isfinite(parsed):
                return min(30.0, max(0.0, parsed))
            if math.isfinite(parsed) and parsed >= 0:
                return min(30.0, parsed)
        return float(min(8.0, self._base_backoff_seconds * 2**attempt))

    async def _wait_before_retry(
        self, response: httpx.Response | None, attempt: int, outcome: str
    ) -> None:
        delay = self._retry_delay(response, attempt)
        self.last_retry_wait_ms += delay * 1000
        rerank_provider_attempts_total.labels(outcome=outcome).inc()
        rerank_retry_wait_seconds.observe(delay)
        await self._sleep(delay)

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        self.last_search_units = 0
        self.last_attempts = 0
        self.last_retry_wait_ms = 0.0
        self.last_provider_latency_ms = 0.0
        self.last_local_latency_ms = 0.0
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=30.0, transport=self._transport
        ) as client:
            for attempt in range(self._max_retries + 1):
                self.last_attempts = attempt + 1
                provider_started = perf_counter()
                try:
                    response = await client.post(
                        "/v2/rerank",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={
                            "model": self._model,
                            "query": query,
                            "documents": texts,
                            "top_n": len(texts),
                        },
                    )
                except httpx.HTTPError as exc:
                    provider_elapsed = perf_counter() - provider_started
                    self.last_provider_latency_ms += provider_elapsed * 1000
                    rerank_provider_duration_seconds.observe(provider_elapsed)
                    if attempt < self._max_retries:
                        await self._wait_before_retry(
                            None, attempt, "transport_retry"
                        )
                        continue
                    rerank_provider_attempts_total.labels(
                        outcome="transport_exhausted"
                    ).inc()
                    raise RerankUnavailable(
                        "cohere reranker transport failed after retries"
                    ) from exc
                provider_elapsed = perf_counter() - provider_started
                self.last_provider_latency_ms += provider_elapsed * 1000
                rerank_provider_duration_seconds.observe(provider_elapsed)
                if not 200 <= response.status_code < 300:
                    if (
                        response.status_code in _COHERE_RETRYABLE_STATUSES
                        and attempt < self._max_retries
                    ):
                        await self._wait_before_retry(
                            response, attempt, "status_retry"
                        )
                        continue
                    outcome = (
                        "status_exhausted"
                        if response.status_code in _COHERE_RETRYABLE_STATUSES
                        else "status_non_retryable"
                    )
                    rerank_provider_attempts_total.labels(outcome=outcome).inc()
                    raise RerankUnavailable(
                        f"cohere reranker returned HTTP {response.status_code}"
                    )
                local_started = perf_counter()
                try:
                    body = response.json()
                    scores = [0.0] * len(texts)
                    for item in body["results"]:
                        idx = int(item["index"])
                        if idx < 0 or idx >= len(texts):
                            raise IndexError(
                                f"cohere returned out-of-bounds index {idx}"
                            )
                        scores[idx] = float(item["relevance_score"])
                    self.last_search_units = _search_units(body)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    rerank_provider_attempts_total.labels(
                        outcome="payload_invalid"
                    ).inc()
                    raise RerankUnavailable(
                        "cohere reranker returned an unusable response"
                    ) from exc
                self.last_local_latency_ms += (perf_counter() - local_started) * 1000
                rerank_provider_attempts_total.labels(outcome="success").inc()
                return scores
        raise AssertionError("unreachable Cohere retry loop")


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
        return CohereReranker(
            base_url="https://api.cohere.com",
            api_key=key,
            model=model,
            max_retries=settings.cohere_rerank_max_retries,
            base_backoff_seconds=settings.cohere_rerank_base_backoff_seconds,
        )
    if settings.rerank_backend == "lexical":
        return LexicalReranker()
    return TeiReranker(settings.rerank_url)
