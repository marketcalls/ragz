import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Protocol

import httpx
from qdrant_client import models

from raghub.core.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DenseEmbedder(Protocol):
    """Seam that makes dense embeddings stubbable (tests use HashDenseEmbedder)."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


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
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=60.0, transport=self._transport
        ) as client:
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                r = await client.post("/embed", json={"inputs": batch, "truncate": True})
                r.raise_for_status()
                out.extend(r.json())
        return out


class HashDenseEmbedder:
    """Deterministic stand-in for TEI (test/dev only): L2-normalized bag of hashed
    unigrams. Texts sharing words get high cosine; disjoint texts get ~0. With this
    backend, "semantic" similarity reduces to lexical overlap — tests are scoped
    accordingly; true semantic quality is validated in the real-stack smoke."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).hexdigest()
            vec[int(digest, 16) % self._dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@lru_cache
def get_dense_embedder() -> DenseEmbedder:
    settings = get_settings()
    if settings.embedding_backend == "hash":
        return HashDenseEmbedder(dim=settings.embedding_dim)
    return TeiDenseEmbedder(settings.tei_url)


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
