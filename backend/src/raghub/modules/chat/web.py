"""Tavily web-search client (Phase 3 D7).

Iron rule 3 note: FIFTH sanctioned caller of secrets._get_secret_decrypted —
the superadmin-stored key (secret name "tavily", written via the existing
PUT /api/v1/admin/secrets/tavily) is decrypted in memory for exactly one
outbound request and never returned, logged, or persisted. Named in the
allowlist test (tests/modules/models/test_sync.py) — the ONLY allowlist
change in Phase 3.

Iron rule 5 note: results are untrusted DATA. This module returns structured
WebResults only; they reach the model exclusively through the production
<data>-block rendering in prompting.py (escaped), and reach the UI as
text + plain hrefs.

Iron rule 1 note: no Qdrant here — pinned by tests/isolation/test_agent_isolation.py.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import UpstreamError
from raghub.modules.secrets import service as secrets_service

TAVILY_SECRET_NAME = "tavily"  # noqa: S105 - a secret NAME, not a secret
_MAX_RESULTS = 5
_SNIPPET_CHARS = 500


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


class WebSearcher(Protocol):
    """Injectable seam (mirrors Retriever/LLMStreamer): tests fake it, the
    routes construct TavilySearcher."""

    async def __call__(self, session: AsyncSession, query: str) -> list[WebResult]: ...


class TavilySearcher:
    def __init__(
        self, *, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def __call__(self, session: AsyncSession, query: str) -> list[WebResult]:
        key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session, name=TAVILY_SECRET_NAME, settings=self._settings
        )
        payload = {"query": query, "max_results": _MAX_RESULTS}
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(
                base_url=self._settings.tavily_url, transport=self._transport,
                timeout=httpx.Timeout(20.0, connect=5.0),
            ) as client:
                response = await client.post("/search", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise UpstreamError("web search provider unreachable") from exc
        if response.status_code != 200:
            raise UpstreamError(f"web search provider returned {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError("malformed web search response") from exc
        results: list[WebResult] = []
        for item in body.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue  # defense: only real links become citable sources
            results.append(
                WebResult(
                    title=str(item.get("title") or url)[:200],
                    url=url,
                    snippet=str(item.get("content") or "")[:_SNIPPET_CHARS],
                )
            )
        return results
