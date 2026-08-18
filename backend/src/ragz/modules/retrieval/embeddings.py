import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID

import httpx
from qdrant_client import models

from ragz.core.config import get_settings
from ragz.core.errors import UpstreamError

_TOKEN_RE = re.compile(r"[a-z0-9]+")


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
                r.raise_for_status()
                out.extend(r.json())
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
        transport: httpx.AsyncBaseTransport | None = None,
        batch_size: int = 32,
    ) -> None:
        self._base_url = base_url
        self._master_key = master_key
        self._model = model
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
                    response = await client.post(
                        "/v1/embeddings",
                        json={"model": self._model, "input": batch},
                        headers=headers,
                    )
                    if response.status_code != 200:
                        body_str = response.text[:200]
                        raise UpstreamError(
                            f"embedding gateway returned {response.status_code}: {body_str}"
                        )
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise UpstreamError("malformed embedding response from gateway") from exc
                    ordered = sorted(body.get("data", []), key=lambda d: d["index"])
                    out.extend([d["embedding"] for d in ordered])
                    # Hosted providers return billed usage; missing/malformed
                    # usage degrades to 0 (a cost undercount is never a failure).
                    usage = body.get("usage") or {}
                    try:
                        total_tokens += int(usage.get("total_tokens") or 0)
                    except (TypeError, ValueError):
                        pass
        except httpx.HTTPError as exc:
            raise UpstreamError("embedding gateway unreachable") from exc
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
    model_id: UUID, *, provider_kind: str, litellm_model_name: str
) -> DenseEmbedder:
    """DOC-10: model-parameterized (was a no-arg global singleton). Cached by
    the primitive (model_id, provider_kind, litellm_model_name) tuple, not by
    an ORM Model object -- two Model instances loaded in different sessions
    for the SAME row don't share Python identity/hash, which would defeat
    lru_cache's whole purpose across separate Celery task invocations.

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
        model=litellm_model_name,
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
