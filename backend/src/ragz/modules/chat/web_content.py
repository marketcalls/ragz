"""SSRF-guarded full-page-text fetch that enriches web-search snippets with
the actual page content, so the model reasons over real page text instead of a
~500-char provider excerpt (the "the excerpt does not list the brokers" gap).

Iron rule 5 / SSRF note: this is the second place (after media.fetch_image_safely)
that dials an attacker-influenceable URL server-side (a web-search RESULT url).
It REUSES media.py's already-hardened guard verbatim -- `media._resolve_safe`
(resolve host once, reject any private/loopback/link-local/reserved/multicast/
unspecified/ipv4-mapped IP) runs BEFORE any network I/O, and `media._pin_transport`
pins the socket to the validated literal IP (DNS-rebind pin, TLS-safe). Any
redirect is re-resolved + re-validated against the same guard before the one
permitted follow-up. No SSRF logic is reimplemented here.

The result text reaches the model exclusively as untrusted DATA through the
same WebResult -> <data>-block rendering as the original snippet (iron rule 5);
this module only makes the snippet LONGER (real page text), never changes how
it is escaped or where it flows.

Never raises: every fetch failure (blocked host, wrong content-type, oversize
body, network error, decode/parse failure) degrades to None, exactly like the
best-effort image fetch -- a failed enrichment simply leaves the original
short snippet in place.
"""

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from ragz.modules.chat.media import (
    _REDIRECT_STATUS_CODES,
    _pin_transport,
    _resolve_safe,
)
from ragz.modules.chat.web import WebResult

logger = structlog.get_logger()

_DEFAULT_MAX_BYTES = 2_000_000
_DEFAULT_MAX_CHARS = 6000
_ALLOWED_CONTENT_TYPES = ("text/html", "text/plain")
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
# Drop <script>/<style> blocks wholesale before the generic tag-strip fallback,
# so their contents never leak into the extracted text.
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


async def _read_capped_text(response: httpx.Response, max_bytes: int) -> str | None:
    """Content-type + size gate + STREAMING read. Header checks run BEFORE the
    body is consumed; the body is accumulated chunk-by-chunk and aborted the
    instant it exceeds `max_bytes` (mirrors media._read_capped_body, but for
    text/html instead of image/*)."""
    if response.status_code != 200:
        return None
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith(_ALLOWED_CONTENT_TYPES):
        return None
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                return None
        except ValueError:
            return None
    buf = bytearray()
    async for chunk in response.aiter_bytes():
        buf += chunk
        if len(buf) > max_bytes:
            return None
    return buf.decode("utf-8", errors="replace")


def _extract_text(html: str, max_chars: int) -> str | None:
    """Main-content extraction via trafilatura, with a tag-strip fallback if
    trafilatura returns nothing. Collapse whitespace + truncate to max_chars.
    None if there is no usable text."""
    text: str | None = None
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=True)
    except Exception:  # noqa: BLE001 - extractor must never take down the fetch
        text = None
    if not text:
        stripped = _SCRIPT_STYLE_RE.sub(" ", html)
        text = _TAG_RE.sub(" ", stripped)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return None
    return text[:max_chars]


async def fetch_page_text(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_chars: int = _DEFAULT_MAX_CHARS,
    timeout: httpx.Timeout | None = None,
) -> str | None:
    """Fetch `url` under the media.py SSRF guard and return its extracted
    main-content text (or None on ANY failure -- never raises).

    Steps: require http/https + non-empty host; `_resolve_safe(host)` (blocked
    -> None BEFORE any network I/O); pin the socket to the validated IP;
    stream one GET with redirects OFF; on a redirect status with a Location,
    re-resolve + re-validate the target host and do ONE more GET (no further
    redirects); require a 200 + text/html|text/plain content-type before
    reading; stream the body under the byte cap; extract + truncate the text.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    ips = _resolve_safe(parsed.hostname)
    if ips is None:
        return None

    client_timeout = timeout or httpx.Timeout(15.0, connect=5.0)
    try:
        client = httpx.AsyncClient(
            transport=_pin_transport(transport, ips, parsed.hostname),
            timeout=client_timeout,
            follow_redirects=False,
        )
        async with client:
            async with client.stream("GET", url) as response:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    redirect_url = urljoin(url, location)
                    redirect_parsed = urlparse(redirect_url)
                    if redirect_parsed.scheme not in ("http", "https") or (
                        not redirect_parsed.hostname
                    ):
                        return None
                    redirect_ips = _resolve_safe(redirect_parsed.hostname)
                    if redirect_ips is None:
                        return None
                    html = await _fetch_once(
                        redirect_url,
                        hostname=redirect_parsed.hostname,
                        ips=redirect_ips,
                        transport=transport,
                        timeout=client_timeout,
                        max_bytes=max_bytes,
                    )
                else:
                    html = await _read_capped_text(response, max_bytes)
    except httpx.HTTPError:
        return None
    if html is None:
        return None
    return _extract_text(html, max_chars)


async def _fetch_once(
    url: str,
    *,
    hostname: str,
    ips: list[str],
    transport: httpx.AsyncBaseTransport | None,
    timeout: httpx.Timeout,
    max_bytes: int,
) -> str | None:
    """One streaming GET (no redirects followed) against an already-validated +
    pinned host -- used for the single permitted redirect follow-up."""
    client = httpx.AsyncClient(
        transport=_pin_transport(transport, ips, hostname),
        timeout=timeout,
        follow_redirects=False,
    )
    async with client:
        async with client.stream("GET", url) as response:
            # A second redirect is NOT followed (only 200 text is accepted here).
            return await _read_capped_text(response, max_bytes)


async def enrich_with_page_content(
    results: list[WebResult],
    *,
    limit: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[WebResult]:
    """For the FIRST `limit` results, fetch full page text concurrently and
    replace the short snippet with it when the fetch yields non-empty text.
    Order preserved; failed fetches and results beyond `limit` are unchanged."""
    if limit <= 0 or not results:
        return results
    head = results[:limit]
    texts = await asyncio.gather(
        *(fetch_page_text(r.url, transport=transport) for r in head),
        return_exceptions=True,
    )
    enriched: list[WebResult] = []
    for result, text in zip(head, texts, strict=True):
        if isinstance(text, str) and text:
            enriched.append(
                WebResult(
                    title=result.title,
                    url=result.url,
                    snippet=text,
                    image_url=result.image_url,
                )
            )
        else:
            enriched.append(result)
    enriched.extend(results[limit:])
    return enriched
