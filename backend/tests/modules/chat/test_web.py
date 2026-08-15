"""Web-search clients: Tavily (D7, the key round-trips through the secrets
module -- these tests exercise the real encrypt/decrypt path against the test
KEK) and DuckDuckGoSearcher (DDG-01, the default keyless provider -- the ddgs
client is monkeypatched so these tests never hit the real network)."""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import NotFoundError, UpstreamError
from ragz.modules.chat.web import (
    TAVILY_SECRET_NAME,
    DuckDuckGoSearcher,
    TavilySearcher,
    WebResult,
)
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


def test_billable_flags_distinguish_paid_from_free_provider(
    test_settings: Settings,
) -> None:
    # Cost reporting (design 2026-08-15): Tavily is metered, DuckDuckGo is free.
    assert TavilySearcher(settings=test_settings).billable is True
    assert DuckDuckGoSearcher().billable is False


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


# --- DuckDuckGoSearcher (DDG-01: default, keyless provider) -----------------


class _FakeDDGS:
    """Stand-in for ddgs.DDGS -- a context manager whose .text() is scripted
    per-test, never touching the network. Records the thread it ran on so
    tests can assert DuckDuckGoSearcher.__call__ actually ran it off the
    event loop (asyncio.to_thread), not inline."""

    instances: list["_FakeDDGS"] = []

    def __init__(self, *, results: list[dict[str, Any]] | None = None,
                 exc: Exception | None = None) -> None:
        self._results = results if results is not None else []
        self._exc = exc
        self.calls: list[tuple[str, int | None]] = []
        self.thread_id: int | None = None

    def __enter__(self) -> "_FakeDDGS":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        import threading

        self.thread_id = threading.get_ident()
        self.calls.append((query, kwargs.get("max_results")))
        if self._exc is not None:
            raise self._exc
        return self._results


def _patch_ddgs(monkeypatch: pytest.MonkeyPatch, fake: _FakeDDGS) -> None:
    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", lambda: fake)


async def test_duckduckgo_maps_results_to_web_result_shape(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeDDGS(results=[
        {"title": "ISO 45001 overview", "href": "https://example.com/iso",
         "body": "ISO 45001 is an occupational health standard."},
        {"title": "Second hit", "href": "https://example.com/second", "body": "More text."},
    ])
    _patch_ddgs(monkeypatch, fake)

    searcher = DuckDuckGoSearcher()
    results = await searcher(session, "ISO 45001")

    assert results == [
        WebResult(title="ISO 45001 overview", url="https://example.com/iso",
                   snippet="ISO 45001 is an occupational health standard."),
        WebResult(title="Second hit", url="https://example.com/second", snippet="More text."),
    ]
    assert fake.calls == [("ISO 45001", 5)]  # default cap (_MAX_RESULTS)


async def test_duckduckgo_drops_results_without_a_real_url(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeDDGS(results=[
        {"title": "no href", "href": "", "body": "junk"},
        {"title": "ftp not http(s)", "href": "ftp://example.com/x", "body": "junk"},
        {"title": "good", "href": "https://example.com/good", "body": "kept"},
    ])
    _patch_ddgs(monkeypatch, fake)

    results = await DuckDuckGoSearcher()(session, "anything")
    assert [r.url for r in results] == ["https://example.com/good"]


async def test_duckduckgo_runs_off_the_event_loop_thread(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    fake = _FakeDDGS(results=[])
    _patch_ddgs(monkeypatch, fake)

    main_thread_id = threading.get_ident()
    await DuckDuckGoSearcher()(session, "anything")
    assert fake.thread_id is not None
    assert fake.thread_id != main_thread_id


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("rate limited"), httpx.ConnectError("network down"), ImportError("no ddgs")],
)
async def test_duckduckgo_returns_empty_list_on_any_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    fake = _FakeDDGS(exc=exc)
    _patch_ddgs(monkeypatch, fake)

    results = await DuckDuckGoSearcher()(session, "anything")
    assert results == []


async def test_duckduckgo_missing_import_returns_empty_list(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DDG-01: an uninstalled/broken ddgs package must degrade this ONE
    # provider to "no results", not break chat module import or raise out of
    # the searcher -- the import happens lazily inside the threaded call, so
    # simulating "package absent" via sys.modules exercises that exact path.
    import sys

    monkeypatch.setitem(sys.modules, "ddgs", None)
    results = await DuckDuckGoSearcher()(session, "anything")
    assert results == []


async def test_duckduckgo_never_calls_secrets_service(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keyless by construction: unlike TavilySearcher, nothing here should
    # ever reach modules/secrets -- this pins the "works with NO secret
    # configured" requirement independent of any use_web-gate test.
    fake = _FakeDDGS(results=[
        {"title": "t", "href": "https://example.com/x", "body": "b"},
    ])
    _patch_ddgs(monkeypatch, fake)

    async def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("DuckDuckGoSearcher must never decrypt a secret")

    monkeypatch.setattr(secrets_service, "_get_secret_decrypted", _boom)
    results = await DuckDuckGoSearcher()(session, "anything")
    assert len(results) == 1
