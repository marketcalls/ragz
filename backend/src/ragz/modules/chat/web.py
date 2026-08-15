"""Web-search clients: DuckDuckGo (default, keyless) + Tavily (Phase 3 D7,
optional, requires a stored API key).

Iron rule 3 note: TavilySearcher is a sanctioned caller of
secrets._get_secret_decrypted — the superadmin-stored key (secret name
"tavily", written via the existing PUT /api/v1/admin/secrets/tavily) is
decrypted in memory for exactly one outbound request and never returned,
logged, or persisted. Named in the allowlist test
(tests/modules/models/test_sync.py). DuckDuckGoSearcher is keyless and never
touches the secrets module at all.

Iron rule 5 note: results are untrusted DATA. This module returns structured
WebResults only; they reach the model exclusively through the production
<data>-block rendering in prompting.py (escaped), and reach the UI as
text + plain hrefs.

Iron rule 1 note: no Qdrant here — pinned by tests/isolation/test_agent_isolation.py.

RAGZ-PUB-08 remediation (stored prompt injection steering the outbound web
query, items 1 & 3): `build_web_search_query` and `redact_query` are the taint
boundary between "text the planner LLM produced" (which may be laundering
retrieved document content — iron rule 5's untrusted DATA) and "text that
leaves Ragz to a third-party provider". Every caller of a WebSearcher
implementation (TavilySearcher or DuckDuckGoSearcher) MUST route the outgoing
query through both before it ever reaches `__call__`; `modules/chat/agent.py`'s
`execute_tool` is the one production caller and does exactly that (see its
`web_search` branch) — this module only changes WHICH provider runs, never
that taint boundary.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.config import Settings
from ragz.core.errors import NotFoundError, UpstreamError
from ragz.modules.secrets import service as secrets_service

logger = structlog.get_logger()

TAVILY_SECRET_NAME = "tavily"  # noqa: S105 - a secret NAME, not a secret
_MAX_RESULTS = 5
_SNIPPET_CHARS = 500

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def build_web_search_query(user_question: str, requested: str) -> str:
    """RAGZ-PUB-08 items 1 & 3: build the outgoing query from the user's
    ORIGINAL question, never from the planner's raw `requested` text.
    `requested` is model output, and the model's own context window includes
    retrieved-document summaries (agent.py's `_outcome_summary`) — a stored
    prompt injection in a document can steer the model into putting document
    text (or another user's leaked content) into `requested`.

    Constrained transform: keep only whitespace-delimited tokens from
    `requested` that ALSO occur, case-insensitively, in `user_question`.
    Tokens the model added that are not already in the user's own words are
    dropped outright. If nothing survives the intersection, fall back to
    `user_question` verbatim.

    INVARIANT (the taint boundary): every token in the return value is
    provably a substring of `user_question` — a token that exists ONLY in
    `requested` (e.g. because it was copied from a retrieved chunk) can never
    appear in the output, by construction.
    """
    question_tokens = {t.lower() for t in _WORD_RE.findall(user_question)}
    if not question_tokens:
        return user_question.strip()
    seen: set[str] = set()
    kept: list[str] = []
    for token in _WORD_RE.findall(requested):
        low = token.lower()
        if low in question_tokens and low not in seen:
            seen.add(low)
            kept.append(token)
    return " ".join(kept) if kept else user_question.strip()


# Conservative, regex-based secret/PII redaction (item 3). Order matters:
# specific shapes (bearer tokens, emails, provider-prefixed keys, key=value
# assignments) run before the generic high-entropy catch-all so their
# placeholders read cleanly rather than getting partially re-redacted.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"bearer\s+[a-z0-9._-]{8,}", re.IGNORECASE), "[REDACTED-TOKEN]"),
    (re.compile(r"[a-z0-9_.+-]+@[a-z0-9-]+\.[a-z0-9.-]+", re.IGNORECASE), "[REDACTED-EMAIL]"),
    (re.compile(r"sk-[a-z0-9]{10,}", re.IGNORECASE), "[REDACTED-KEY]"),
    (re.compile(r"ghp_[a-z0-9]{20,}", re.IGNORECASE), "[REDACTED-KEY]"),
    (
        re.compile(
            r"(?:api[_-]?key|api[_-]?secret|access[_-]?token|secret|password|passwd)"
            r"\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
        "[REDACTED-SECRET]",
    ),
    (re.compile(r"\b[a-z0-9]{32,}\b", re.IGNORECASE), "[REDACTED-TOKEN]"),
)


def redact_query(query: str) -> str:
    """RAGZ-PUB-08 item 3: strip obvious secret/PII shapes from a query before
    it leaves Ragz — emails, bearer tokens, provider-prefixed API keys,
    `key=value`-style secret assignments, and generic high-entropy (32+ char)
    tokens. Conservative by design: over-redacting is safe, under-redacting
    is the bug we're fixing. Never logs or returns the pre-redaction text."""
    redacted = query
    for pattern, placeholder in _REDACT_PATTERNS:
        redacted = pattern.sub(placeholder, redacted)
    return redacted.strip()


def has_searchable_content(redacted_query: str) -> bool:
    """True if `redacted_query` still has a real search term left after
    redaction — i.e. something other than whitespace and our own
    `[REDACTED-*]` placeholders. Callers must skip the search (never send a
    redaction-only or empty query) when this is False."""
    stripped = re.sub(r"\[REDACTED-[A-Z]+\]", "", redacted_query)
    return _WORD_RE.search(stripped) is not None


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


class WebSearcher(Protocol):
    """Injectable seam (mirrors Retriever/LLMStreamer): tests fake it, the
    routes construct TavilySearcher."""

    # Cost reporting (design 2026-08-15): True for a paid provider whose calls
    # should be metered (feature="web_search", units=1/call); False for a free
    # provider (DuckDuckGo) that records nothing.
    billable: bool

    async def __call__(self, session: AsyncSession, query: str) -> list[WebResult]: ...


class TavilySearcher:
    billable = True  # paid provider -- each call is a metered web_search unit

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


class DuckDuckGoSearcher:
    """Default, keyless web-search provider (ddgs, the maintained successor to
    duckduckgo-search). No secret, no superadmin config, no PUT
    /api/v1/admin/secrets/* round-trip — this is what makes web search work
    out of the box (see the use_web gate in modules/chat/service.py, which no
    longer requires a Tavily secret to offer web_search to the planner).

    Same WebSearcher interface and same WebResult shape as TavilySearcher, so
    `modules/chat/agent.py`'s execute_tool (the PUB-08 consent/budget/
    redaction pipeline) treats both identically — this class only supplies
    the provider call, never the taint boundary.

    ddgs's DDGS().text(...) is a blocking, synchronous call (it shells out to
    an HTTP client itself, not httpx/asyncio) -- run off the event loop via
    asyncio.to_thread, the same pattern this codebase already uses for other
    CPU/blocking work (documents/pipeline.py, retrieval/service.py).

    Graceful-degradation contract (mirrors TavilySearcher's role in
    execute_tool, but enforced HERE instead of via a raised UpstreamError):
    any failure -- import error, network error, rate limit, malformed
    response -- returns an empty list rather than raising. web_search is a
    best-effort fallback tool; a keyless, unauthenticated, best-effort
    provider degrading to "no results" (and letting the agent loop fall back
    to single-shot RAG / no-answer) is safer than surfacing raised errors
    from an unmonitored third party."""

    billable = False  # keyless, free provider -- never metered for cost

    def __init__(self, *, max_results: int = _MAX_RESULTS) -> None:
        self._max_results = max_results

    async def __call__(self, session: AsyncSession, query: str) -> list[WebResult]:
        try:
            raw_results = await asyncio.to_thread(self._search_sync, query)
        except Exception:
            logger.info("chat.web_search.duckduckgo_failed", query_len=len(query))
            return []
        results: list[WebResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("href") or "")
            if not url.startswith(("http://", "https://")):
                continue  # defense: only real links become citable sources
            results.append(
                WebResult(
                    title=str(item.get("title") or url)[:200],
                    url=url,
                    snippet=str(item.get("body") or "")[:_SNIPPET_CHARS],
                )
            )
        return results

    def _search_sync(self, query: str) -> list[dict[str, object]]:
        # Imported lazily inside the thread call (not at module top-level) so
        # a broken/missing ddgs install degrades this ONE provider to "no
        # results" via the except-Exception above, rather than making the
        # whole chat module fail to import.
        from ddgs import DDGS

        with DDGS() as ddgs:
            return ddgs.text(query, max_results=self._max_results)


async def build_web_searcher(
    session: AsyncSession,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebSearcher:
    """Resolve the active web-search provider (mirrors retrieval.get_reranker's
    posture: reads the `web_search_provider` app-setting live, not cached).

    Fallback contract:
    - "duckduckgo" (default) or unset -> DuckDuckGoSearcher (keyless).
    - "tavily" WITH a stored `tavily` secret -> TavilySearcher.
    - "tavily" WITHOUT a stored key -> DuckDuckGoSearcher, logging that Tavily
      was selected but is unconfigured. Web search is a best-effort fallback
      tool, so a missing key degrades to the keyless provider rather than
      raising (unlike the reranker's hard RerankMisconfigured 409).

    This is the ONE sanctioned DB-touching function in this otherwise DB-free
    module (see get_reranker). TavilySearcher decrypts the key itself on call;
    here we only confirm the secret EXISTS (existence-only, no decryption)."""
    provider = await get_app_setting(session, "web_search_provider")
    if provider == "tavily":
        try:
            present = await secrets_service.existing_secret_names(
                session, [TAVILY_SECRET_NAME]
            )
        except NotFoundError:
            present = set()
        if TAVILY_SECRET_NAME in present:
            return TavilySearcher(settings=settings, transport=transport)
        logger.info("chat.web_search.tavily_selected_but_unconfigured")
    return DuckDuckGoSearcher()
