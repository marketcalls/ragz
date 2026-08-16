"""Isolation/security tests for the SSRF-guarded page-text fetch used to
enrich web-search snippets with full page content (web_content.py).

This fetch reuses the SAME SSRF guard as the image proxy (`media._resolve_safe`
+ `media._pin_transport`): resolve the host once, reject any private/loopback/
link-local/reserved IP BEFORE any network I/O, pin the socket to the validated
IP (DNS-rebind pin), and re-validate any redirect target before the one
permitted follow-up. These tests mirror test_media_proxy.py's MockTransport +
monkeypatched getaddrinfo pattern and pin:
  * a private-resolving host is blocked BEFORE the network is touched;
  * a non-text content-type yields None;
  * an oversize body (streamed) is aborted -> None;
  * a small valid text/html body is extracted to text;
  * a redirect to a private host yields None.
"""

import socket
from collections.abc import AsyncIterator

import httpx
import pytest

from ragz.modules.chat import web_content


def _addrinfo(ip: str) -> list[tuple]:
    """Shape of socket.getaddrinfo() output: (family, type, proto, canon, sockaddr)."""
    if ":" in ip:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


async def test_fetch_rejects_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("10.1.2.3"))
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, text="<html><body>hi</body></html>",
                              headers={"content-type": "text/html"})

    out = await web_content.fetch_page_text(
        "https://internal.example.com/page", transport=httpx.MockTransport(handler)
    )
    assert out is None
    assert called["n"] == 0  # blocked BEFORE any network call


async def test_fetch_rejects_non_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    assert await web_content.fetch_page_text("file:///etc/passwd") is None


async def test_fetch_rejects_non_text_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG binary",
                              headers={"content-type": "image/png"})

    out = await web_content.fetch_page_text(
        "https://pub.example.com/x.png", transport=httpx.MockTransport(handler)
    )
    assert out is None


async def test_fetch_rejects_oversize_streamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host streaming a body larger than max_bytes is aborted mid-stream."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    async def _body() -> AsyncIterator[bytes]:
        yield b"<html><body>" + b"x" * 5000 + b"</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=_body())

    out = await web_content.fetch_page_text(
        "https://pub.example.com/big", transport=httpx.MockTransport(handler), max_bytes=1000
    )
    assert out is None


async def test_fetch_extracts_text_from_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    html = (
        "<html><head><title>Brokers</title></head><body>"
        "<article><h1>Top brokers of 2026</h1>"
        "<p>Zerodha is the largest discount broker in India by active clients.</p>"
        "<p>Upstox and Angel One follow closely in the rankings.</p>"
        "</article></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})

    out = await web_content.fetch_page_text(
        "https://pub.example.com/brokers", transport=httpx.MockTransport(handler)
    )
    assert out is not None
    assert "Zerodha" in out
    assert "Angel One" in out


async def test_fetch_redirect_to_private_host_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """An open redirect from a public host to a private host is re-validated
    and rejected before the follow-up request."""

    def _resolve(host: str, *a: object, **k: object) -> list[tuple]:
        if host == "pub.example.com":
            return _addrinfo("93.184.216.34")
        return _addrinfo("169.254.169.254")  # link-local metadata addr

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})

    out = await web_content.fetch_page_text(
        "https://pub.example.com/redir", transport=httpx.MockTransport(handler)
    )
    assert out is None
    assert calls["n"] == 1  # first GET happened, redirect target never fetched


async def test_fetch_follows_redirect_to_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect to another PUBLIC host is followed exactly once and its text
    extracted."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    html = "<html><body><article><p>Final destination content here.</p></article></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://pub.example.com/final"})
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    out = await web_content.fetch_page_text(
        "https://pub.example.com/start", transport=httpx.MockTransport(handler)
    )
    assert out is not None
    assert "Final destination" in out


async def test_enrich_replaces_top_snippets_with_page_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.modules.chat.web import WebResult

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        html = (
            f"<html><body><article><p>Full page body for {request.url.path} "
            "with much more detail than the snippet.</p></article></body></html>"
        )
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    results = [
        WebResult(title=f"r{i}", url=f"https://pub.example.com/{i}", snippet=f"short {i}")
        for i in range(5)
    ]
    enriched = await web_content.enrich_with_page_content(
        results, limit=3, transport=httpx.MockTransport(handler)
    )
    assert len(enriched) == 5
    # Top 3 got their snippets replaced with the fetched full page text.
    for i in range(3):
        assert "Full page body" in enriched[i].snippet
        assert enriched[i].url == results[i].url
        assert enriched[i].title == results[i].title
    # The rest are unchanged.
    for i in range(3, 5):
        assert enriched[i].snippet == f"short {i}"


async def test_enrich_leaves_failed_fetch_snippets_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.modules.chat.web import WebResult

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        # Non-text content-type -> fetch returns None for every result.
        return httpx.Response(200, content=b"nope", headers={"content-type": "image/png"})

    results = [
        WebResult(title="r0", url="https://pub.example.com/0", snippet="keep me"),
    ]
    enriched = await web_content.enrich_with_page_content(
        results, limit=3, transport=httpx.MockTransport(handler)
    )
    assert enriched[0].snippet == "keep me"
