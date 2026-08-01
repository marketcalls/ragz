"""Tavily client (D7). The key round-trips through the secrets module —
these tests exercise the real encrypt/decrypt path against the test KEK."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import NotFoundError, UpstreamError
from ragz.modules.chat.web import TAVILY_SECRET_NAME, TavilySearcher, WebResult
from ragz.modules.secrets import service as secrets_service


async def _store_key(session: AsyncSession, settings: Settings) -> None:
    await secrets_service.set_secret(
        session, actor_id=None, name=TAVILY_SECRET_NAME,
        value="tvly-test-key", settings=settings,
    )


async def test_search_parses_results_and_sends_bearer(
    session: AsyncSession, test_settings: Settings
) -> None:
    await _store_key(session, test_settings)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [
            {"title": "ISO 45001 overview", "url": "https://example.com/iso",
             "content": "ISO 45001 is an occupational health standard."},
        ]})

    searcher = TavilySearcher(settings=test_settings, transport=httpx.MockTransport(handler))
    results = await searcher(session, "ISO 45001")
    assert results == [WebResult(
        title="ISO 45001 overview", url="https://example.com/iso",
        snippet="ISO 45001 is an occupational health standard.",
    )]
    assert seen[0].headers["authorization"] == "Bearer tvly-test-key"
    assert seen[0].url.path == "/search"


async def test_missing_key_raises_not_found(
    session: AsyncSession, test_settings: Settings
) -> None:
    searcher = TavilySearcher(settings=test_settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"results": []})
    ))
    with pytest.raises(NotFoundError):
        await searcher(session, "anything")


async def test_upstream_failure_raises_upstream_error(
    session: AsyncSession, test_settings: Settings
) -> None:
    await _store_key(session, test_settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="tavily down")

    searcher = TavilySearcher(settings=test_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError):
        await searcher(session, "anything")


def test_web_module_is_in_the_decrypt_allowlist() -> None:
    # The allowlist test itself is the enforcement; this pin documents intent
    # locally: web.py IS expected to reference _get_secret_decrypted.
    from pathlib import Path

    import ragz

    src = (Path(ragz.__file__).parent / "modules" / "chat" / "web.py").read_text()
    assert "_get_secret_decrypted" in src
