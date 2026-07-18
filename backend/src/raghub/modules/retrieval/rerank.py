"""Cross-encoder reranking client (PRD CHAT-2 pull-forward, Plan E).

The Reranker Protocol is the test seam; TeiReranker is the only HTTP client
(mocked at the httpx layer — the one sanctioned mock). LexicalReranker is the
deterministic dev/test backend, playing the same role HashDenseEmbedder plays
for dense embeddings: "relevance" reduces to lexical overlap, so tests are
scoped accordingly and true ranking quality is validated in the live smoke.
"""

import re
from functools import lru_cache
from typing import Protocol

import httpx

from raghub.core.config import get_settings

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
        except httpx.HTTPError as exc:
            raise RerankUnavailable(str(exc)) from exc
        scores = [0.0] * len(texts)
        for item in r.json():  # [{"index": i, "score": s}, ...] sorted by score
            scores[int(item["index"])] = float(item["score"])
        return scores


class LexicalReranker:
    """Deterministic stand-in: fraction of query tokens present in the text."""

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        q = set(_TOKEN_RE.findall(query.lower()))
        if not q:
            return [0.0] * len(texts)
        return [
            len(q & set(_TOKEN_RE.findall(t.lower()))) / len(q) for t in texts
        ]


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    if settings.rerank_backend == "lexical":
        return LexicalReranker()
    return TeiReranker(settings.rerank_url)
