import asyncio
import hashlib
import math
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from time import monotonic
from typing import Any, Protocol
from uuid import UUID

import httpx
from qdrant_client import models

from ragz.core.config import Settings, get_settings
from ragz.core.errors import UpstreamError
from ragz.core.metrics import query_embedding_cache_operations_total

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_OPENAI_DIMENSION_MODELS = {"text-embedding-3-small", "text-embedding-3-large"}


def _supports_openai_dimensions(model: str, provider_kind: str | None) -> bool:
    """Return whether the upstream API accepts OpenAI's ``dimensions`` option."""
    if provider_kind is not None and provider_kind != "openai":
        return False
    # LiteLLM accepts both ``text-embedding-3-small`` and provider-prefixed
    # names such as ``openai/text-embedding-3-small``.
    return model.rsplit("/", 1)[-1] in _OPENAI_DIMENSION_MODELS


class DenseEmbedder(Protocol):
    """Seam that makes dense embeddings stubbable (tests use HashDenseEmbedder)."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_with_usage(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """Cost-reporting seam: same vectors as `embed`, plus the hosted API's
        billed token count for THIS call (0 for self-hosted TEI / the test
        hash backend, which cost nothing). Returned, never stashed on the
        instance -- get_dense_embedder lru_caches one shared embedder across
        concurrent requests, so per-call usage must not live in instance
        state (it would race)."""
        ...


class BilledEmbeddingUpstreamError(UpstreamError):
    """Typed provider failure carrying usage completed before the failure."""

    def __init__(self, detail: str, *, billed_tokens: int) -> None:
        super().__init__(detail)
        self.billed_tokens = max(0, billed_tokens)


class QueryEmbeddingCache(Protocol):
    """Request-independent cache seam for query vectors.

    Implementations receive only an opaque model namespace and hash exact query
    text internally. Raw query strings are never used as public cache keys.
    """

    async def get_many(
        self, namespace: str, texts: Sequence[str]
    ) -> list[list[float] | None]: ...

    async def set_many(
        self,
        namespace: str,
        texts: Sequence[str],
        vectors: Sequence[Sequence[float]],
    ) -> None: ...

    async def get_or_compute(
        self,
        namespace: str,
        texts: Sequence[str],
        compute: Callable[
            [list[str]], Awaitable[tuple[list[list[float]], int]]
        ],
    ) -> tuple[list[list[float]], int, int, int, float, float, float]:
        """Return vectors, billing/cache counts, and lookup/store/wait timings."""
        ...


def query_embedding_cache_namespace(
    *,
    org_id: UUID,
    model_id: UUID,
    provider_kind: str,
    model: str,
    dimension: int | None,
) -> str:
    material = f"{org_id}\0{model_id}\0{provider_kind}\0{model}\0{dimension or 0}"
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: float
    vector: tuple[float, ...]


class _SingleFlightOwnerCancelled(Exception):
    """Internal retry signal; never crosses the cache API boundary."""


class InMemoryQueryEmbeddingCache:
    """Bounded, replica-local TTL/LRU cache with opaque SHA-256 keys."""

    def __init__(
        self,
        max_entries: int = 10_000,
        ttl_seconds: float = 3_600,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._max_entries = max_entries
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[tuple[float, ...]]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(namespace: str, text: str) -> str:
        return hashlib.sha256(f"{namespace}\0{text}".encode()).hexdigest()

    async def get_many(
        self, namespace: str, texts: Sequence[str]
    ) -> list[list[float] | None]:
        result: list[list[float] | None] = []
        hits = misses = expired = 0
        async with self._lock:
            now = self._clock()
            for text in texts:
                key = self._key(namespace, text)
                entry = self._entries.get(key)
                if entry is None:
                    misses += 1
                    result.append(None)
                    continue
                if entry.expires_at <= now:
                    self._entries.pop(key, None)
                    expired += 1
                    misses += 1
                    result.append(None)
                    continue
                self._entries.move_to_end(key)
                hits += 1
                result.append(list(entry.vector))
        if hits:
            query_embedding_cache_operations_total.labels(outcome="hit").inc(hits)
        if misses:
            query_embedding_cache_operations_total.labels(outcome="miss").inc(misses)
        if expired:
            query_embedding_cache_operations_total.labels(outcome="expired").inc(expired)
        return result

    async def set_many(
        self,
        namespace: str,
        texts: Sequence[str],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(texts) != len(vectors):
            raise ValueError("cache texts and vectors must have the same length")
        evicted = 0
        async with self._lock:
            expires_at = self._clock() + self._ttl_seconds
            for text, vector in zip(texts, vectors, strict=True):
                key = self._key(namespace, text)
                self._entries[key] = _CacheEntry(
                    expires_at=expires_at,
                    vector=tuple(float(value) for value in vector),
                )
                self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                evicted += 1
        if texts:
            query_embedding_cache_operations_total.labels(outcome="store").inc(len(texts))
        if evicted:
            query_embedding_cache_operations_total.labels(outcome="evicted").inc(evicted)

    async def _publish_owner(
        self,
        owner_keys: list[str],
        normalized: list[tuple[float, ...]],
        owner_futures: list[asyncio.Future[tuple[float, ...]]],
    ) -> int:
        evicted = 0
        async with self._lock:
            expires_at = self._clock() + self._ttl_seconds
            for key, vector, future in zip(
                owner_keys, normalized, owner_futures, strict=True
            ):
                self._entries[key] = _CacheEntry(expires_at=expires_at, vector=vector)
                self._entries.move_to_end(key)
                if self._inflight.get(key) is future:
                    self._inflight.pop(key, None)
                if not future.done():
                    future.set_result(vector)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                evicted += 1
        return evicted

    async def get_or_compute(
        self,
        namespace: str,
        texts: Sequence[str],
        compute: Callable[
            [list[str]], Awaitable[tuple[list[list[float]], int]]
        ],
    ) -> tuple[list[list[float]], int, int, int, float, float, float]:
        """Coalesce identical cold keys while allowing unrelated keys in parallel.

        The provider callback owns only the keys this request reserved. Other
        callers await opaque-key futures under ``shield`` so cancelling one
        waiter cannot cancel the request that is actually paying for the
        provider call.
        """
        if not texts:
            return [], 0, 0, 0, 0.0, 0.0, 0.0

        slots: list[tuple[float, ...] | asyncio.Future[tuple[float, ...]]] = []
        owner_keys: list[str] = []
        owner_texts: list[str] = []
        owner_futures: list[asyncio.Future[tuple[float, ...]]] = []
        hits = misses = expired = coalesced = 0
        loop = asyncio.get_running_loop()
        lookup_started = monotonic()
        async with self._lock:
            now = self._clock()
            for text in texts:
                key = self._key(namespace, text)
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at <= now:
                    self._entries.pop(key, None)
                    entry = None
                    expired += 1
                if entry is not None:
                    self._entries.move_to_end(key)
                    hits += 1
                    slots.append(entry.vector)
                    continue
                misses += 1
                future = self._inflight.get(key)
                if future is None:
                    future = loop.create_future()
                    self._inflight[key] = future
                    owner_keys.append(key)
                    owner_texts.append(text)
                    owner_futures.append(future)
                else:
                    coalesced += 1
                slots.append(future)
        lookup_ms = (monotonic() - lookup_started) * 1000

        if hits:
            query_embedding_cache_operations_total.labels(outcome="hit").inc(hits)
        if misses:
            query_embedding_cache_operations_total.labels(outcome="miss").inc(misses)
        if expired:
            query_embedding_cache_operations_total.labels(outcome="expired").inc(expired)
        if coalesced:
            query_embedding_cache_operations_total.labels(outcome="coalesced").inc(
                coalesced
            )

        billed_tokens = 0
        if owner_texts:
            try:
                computed, billed_tokens = await compute(owner_texts)
                if len(computed) != len(owner_texts):
                    raise UpstreamError("dense embedder returned the wrong vector count")
                normalized = [tuple(float(value) for value in vector) for vector in computed]
            except BaseException as exc:
                async with self._lock:
                    for key, future in zip(owner_keys, owner_futures, strict=True):
                        if self._inflight.get(key) is future:
                            self._inflight.pop(key, None)
                        if future.done():
                            continue
                        if isinstance(exc, asyncio.CancelledError):
                            future.set_exception(_SingleFlightOwnerCancelled())
                        else:
                            future.set_exception(exc)
                # Consume exceptions on owner-created futures even when no
                # waiter survived long enough to observe them.
                await asyncio.gather(*owner_futures, return_exceptions=True)
                raise

            store_started = monotonic()
            publication = asyncio.create_task(
                self._publish_owner(owner_keys, normalized, owner_futures)
            )
            try:
                evicted = await asyncio.shield(publication)
            except asyncio.CancelledError:
                # The provider already completed. Preserve that paid result and
                # resolve every waiter even if the request owner disconnects
                # while publication is waiting for the cache lock.
                publication.add_done_callback(lambda done: done.exception())
                raise
            store_ms = (monotonic() - store_started) * 1000
            query_embedding_cache_operations_total.labels(outcome="store").inc(
                len(owner_texts)
            )
            if evicted:
                query_embedding_cache_operations_total.labels(outcome="evicted").inc(
                    evicted
                )
        else:
            store_ms = 0.0

        owner_future_ids = {id(future) for future in owner_futures}
        resolved: list[list[float]] = []
        wait_ms = 0.0
        for text, slot in zip(texts, slots, strict=True):
            if isinstance(slot, asyncio.Future):
                wait_on_other = id(slot) not in owner_future_ids and not slot.done()
                slot_wait_started = monotonic()
                try:
                    vector = await asyncio.shield(slot)
                except _SingleFlightOwnerCancelled:
                    if wait_on_other:
                        wait_ms += (monotonic() - slot_wait_started) * 1000
                    (
                        retry_vectors,
                        retry_billed,
                        _retry_hits,
                        _retry_misses,
                        retry_lookup_ms,
                        retry_store_ms,
                        retry_wait_ms,
                    ) = await self.get_or_compute(namespace, [text], compute)
                    billed_tokens += retry_billed
                    lookup_ms += retry_lookup_ms
                    store_ms += retry_store_ms
                    wait_ms += retry_wait_ms
                    resolved.append(retry_vectors[0])
                    continue
                if wait_on_other:
                    wait_ms += (monotonic() - slot_wait_started) * 1000
                resolved.append(list(vector))
            else:
                resolved.append(list(slot))
        return (
            resolved,
            billed_tokens,
            hits,
            misses,
            lookup_ms,
            store_ms,
            wait_ms,
        )


@lru_cache(maxsize=16)
def _bounded_query_embedding_cache(
    max_entries: int, ttl_seconds: int
) -> InMemoryQueryEmbeddingCache:
    return InMemoryQueryEmbeddingCache(
        max_entries=max_entries,
        ttl_seconds=ttl_seconds,
    )


def get_query_embedding_cache(
    settings: Settings,
    *,
    enabled_override: bool | None = None,
) -> QueryEmbeddingCache | None:
    """Configured replica-local cache; no raw query leaves this process."""
    enabled = (
        settings.query_embedding_cache_enabled
        if enabled_override is None
        else enabled_override
    )
    if not enabled:
        return None
    return _bounded_query_embedding_cache(
        settings.query_embedding_cache_max_entries,
        settings.query_embedding_cache_ttl_seconds,
    )


def clear_query_embedding_cache() -> None:
    """Drop replica-local entries after settings/test lifecycle changes."""
    _bounded_query_embedding_cache.cache_clear()


class TeiDenseEmbedder:
    """Dense embeddings via a TEI server (bge-m3). Batched HTTP POST /embed."""

    def __init__(
        self,
        base_url: str,
        batch_size: int = 32,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._batch_size = batch_size
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        # Generous read timeout: on CPU-only TEI a full 32-input bge-m3 batch can
        # take 30-60s (high queue_time), and a timeout mid-ingest forces the whole
        # reindex task to retry from batch 1. Query-time embeds send a single input
        # and return in well under a second, so the large ceiling never bites them.
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=180.0, transport=self._transport
        ) as client:
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                try:
                    r = await client.post("/embed", json={"inputs": batch, "truncate": True})
                except httpx.ConnectError as exc:
                    # Issue #1: the bare httpx message is
                    # "All connection attempts failed", which names neither the
                    # service nor the fix. This is the single most likely
                    # first-run failure -- the workspace selects the built-in
                    # local model, but TEI sits behind the local-embeddings
                    # Compose profile, so it is simply not running.
                    raise UpstreamError(
                        f"the local embedding service (TEI) is unreachable at "
                        f"{self._base_url}. Start it with `docker compose -f "
                        f"deploy/compose.yaml --profile local-embeddings up -d tei`, "
                        f"or select a hosted embedding model for this workspace in "
                        f"Admin > Settings > Embedding."
                    ) from exc
                except httpx.HTTPError as exc:
                    raise UpstreamError("local embedding service request failed") from exc
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise UpstreamError(
                        f"local embedding service returned HTTP {r.status_code}"
                    ) from exc
                try:
                    body = r.json()
                except ValueError as exc:
                    raise UpstreamError("malformed response from local embedding service") from exc
                if not isinstance(body, list) or any(
                    not isinstance(vector, list) for vector in body
                ):
                    raise UpstreamError("malformed response from local embedding service")
                out.extend(body)
        return out

    async def embed_with_usage(self, texts: list[str]) -> tuple[list[list[float]], int]:
        # Self-hosted TEI bills nothing -- report 0 tokens (free).
        return await self.embed(texts), 0


class LiteLLMEmbedder:
    """Dense embeddings via the SAME LiteLLM gateway chat already uses (DOC-10),
    mirroring modules/chat/llm.py::LiteLLMStreamer's httpx/error pattern exactly
    but POSTing to /v1/embeddings. Covers OpenAI, Google, Cohere, Voyage AI, and
    anything else the gateway routes -- one class, no per-provider SDK code."""

    def __init__(
        self,
        *,
        base_url: str,
        master_key: str,
        model: str,
        provider_kind: str | None = None,
        dimension: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        batch_size: int = 32,
    ) -> None:
        self._base_url = base_url
        self._master_key = master_key
        self._model = model
        self._provider_kind = provider_kind
        self._dimension = dimension
        self._transport = transport
        self._batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors, _ = await self.embed_with_usage(texts)
        return vectors

    async def embed_with_usage(self, texts: list[str]) -> tuple[list[list[float]], int]:
        headers = {"Authorization": f"Bearer {self._master_key}"}
        out: list[list[float]] = []
        total_tokens = 0
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, transport=self._transport,
                timeout=httpx.Timeout(60.0, connect=10.0),
            ) as client:
                for i in range(0, len(texts), self._batch_size):
                    batch = texts[i : i + self._batch_size]
                    payload: dict[str, object] = {"model": self._model, "input": batch}
                    if self._dimension is not None and _supports_openai_dimensions(
                        self._model, self._provider_kind
                    ):
                        payload["dimensions"] = self._dimension
                    response = await client.post(
                        "/v1/embeddings",
                        json=payload,
                        headers=headers,
                    )
                    if response.status_code != 200:
                        raise BilledEmbeddingUpstreamError(
                            f"embedding gateway returned HTTP {response.status_code}",
                            billed_tokens=total_tokens,
                        )
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise BilledEmbeddingUpstreamError(
                            "malformed embedding response from gateway",
                            billed_tokens=total_tokens,
                        ) from exc
                    if not isinstance(body, dict):
                        raise BilledEmbeddingUpstreamError(
                            "malformed embedding response from gateway",
                            billed_tokens=total_tokens,
                        )
                    usage = body.get("usage")
                    if usage is None:
                        batch_tokens = 0
                    elif isinstance(usage, dict):
                        try:
                            batch_tokens = int(usage.get("total_tokens") or 0)
                        except (TypeError, ValueError, OverflowError):
                            batch_tokens = 0
                    else:
                        raise BilledEmbeddingUpstreamError(
                            "malformed embedding response from gateway",
                            billed_tokens=total_tokens,
                        )
                    total_tokens += max(0, batch_tokens)
                    data = body.get("data")
                    if not isinstance(data, list) or any(
                        not isinstance(item, dict)
                        or not isinstance(item.get("index"), int)
                        or not isinstance(item.get("embedding"), list)
                        or any(
                            isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(float(value))
                            for value in item["embedding"]
                        )
                        for item in data
                    ):
                        raise BilledEmbeddingUpstreamError(
                            "malformed embedding response from gateway",
                            billed_tokens=total_tokens,
                        )
                    ordered = sorted(data, key=lambda item: item["index"])
                    for item in ordered:
                        embedding = item["embedding"]
                        if self._dimension is not None and len(embedding) != self._dimension:
                            raise BilledEmbeddingUpstreamError(
                                "embedding gateway returned vector width "
                                f"{len(embedding)}; expected {self._dimension} dimensions",
                                billed_tokens=total_tokens,
                            )
                        out.append(embedding)
        except httpx.HTTPError as exc:
            raise BilledEmbeddingUpstreamError(
                "embedding gateway unreachable", billed_tokens=total_tokens
            ) from exc
        return out, total_tokens


class HashDenseEmbedder:
    """Deterministic stand-in for TEI (test/dev only): L2-normalized bag of hashed
    unigrams. Texts sharing words get high cosine; disjoint texts get ~0. With this
    backend, "semantic" similarity reduces to lexical overlap — tests are scoped
    accordingly; true semantic quality is validated in the real-stack smoke."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    async def embed_with_usage(self, texts: list[str]) -> tuple[list[list[float]], int]:
        # Deterministic test/dev backend: no hosted API, no billed tokens.
        return await self.embed(texts), 0

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).hexdigest()
            vec[int(digest, 16) % self._dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@lru_cache
def get_dense_embedder(
    model_id: UUID, *, provider_kind: str, litellm_model_name: str,
    dimension: int | None = None,
) -> DenseEmbedder:
    """DOC-10: model-parameterized (was a no-arg global singleton). Cached by
    the primitive (model_id, provider_kind, litellm_model_name, dimension) tuple,
    not by an ORM Model object -- two Model instances loaded in different
    sessions for the SAME row don't share Python identity/hash, which would
    defeat lru_cache's whole purpose across separate Celery task invocations.
    The requested width is part of the key so changing dimensions cannot reuse
    an embedder configured for another vector space.

    settings.embedding_backend == "hash" is a TEST-ONLY override (unchanged
    from before DOC-10): it forces every model_id to the deterministic hash
    embedder regardless of provider_kind, so the existing test suite's
    RAGZ_EMBEDDING_BACKEND=hash env var keeps working unmodified."""
    settings = get_settings()
    if settings.embedding_backend == "hash":
        return HashDenseEmbedder(dim=settings.embedding_dim)
    if provider_kind == "tei":
        return TeiDenseEmbedder(settings.tei_url)
    return LiteLLMEmbedder(
        base_url=settings.litellm_url, master_key=settings.litellm_master_key,
        model=litellm_model_name, provider_kind=provider_kind, dimension=dimension,
    )


@lru_cache
def _bm25_model() -> Any:
    from fastembed import SparseTextEmbedding  # deferred: heavy import

    return SparseTextEmbedding("Qdrant/bm25")


def embed_sparse(texts: list[str]) -> list[models.SparseVector]:
    """BM25-family sparse vectors (ADR-0002). Sync/CPU — wrap in asyncio.to_thread
    from async code."""
    return [
        models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
        for e in _bm25_model().embed(texts)
    ]
