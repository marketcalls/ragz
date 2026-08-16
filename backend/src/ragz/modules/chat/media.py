"""Signed capability URLs for proxied images (generative UI Task 7).

Images are rendered via `<img src>`, which cannot carry the app's Bearer
header, so the browser reaches the proxy route directly and unauthenticated
(see `api/routes/media.py`: it is listed in `PUBLIC_ROUTES`, no
`TenantContext`). Authorization instead lives entirely IN the `image_ref`
itself -- an HMAC-signed, time-limited, self-contained token (mirrors a
signed S3 URL): possession of an unexpired, correctly-signed ref IS the
grant. There is no `generative_media_refs` table and no per-ref DB lookup;
the ref carries everything needed to fetch (`url`) plus auditability fields
(`org_id`, `chat_id`, `message_id`) that travel with it but are never
cross-checked against a caller's own TenantContext (there is no caller
identity here to check against).

Iron rule 3 note: the signing key is derived from the KEK
(`core.crypto.load_kek`), the one out-of-DB secret, via a fixed-context HMAC
-- NOT the raw KEK itself, so a signing-key leak never exposes the KEK, and
this key is domain-separated from every other KEK-derived use (envelope
encryption, JWT signing) by the `b"image-proxy-ref-v1"` context string.

Iron rule 5 / SSRF note: `fetch_image_safely` is the one place that dials an
attacker-influenceable URL server-side (a web-search result image, or
anything else surfaced into an `image_ref`). `_host_is_blocked` MUST run
before any network I/O, and any redirect the upstream returns is re-checked
against the same guard before the one permitted follow-up request -- an
open redirect on an otherwise-public host can never be used to reach
localhost/link-local/metadata addresses.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import socket
import time
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx
from PIL import Image

from ragz.core.config import Settings
from ragz.core.crypto import load_kek

_SIGNING_CONTEXT = b"image-proxy-ref-v1"
_DEFAULT_TTL_SECONDS = 86_400
_DEFAULT_MAX_BYTES = 5_000_000
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_CACHE_TTL_SECONDS = 86_400
# Short-TTL negative cache for refs whose fetch failed -- stops an
# unauthenticated ref from re-driving the upstream SSRF-probe on every replay
# for the full 24h positive-cache window (see `get_or_fetch_image`).
_NEG_CACHE_TTL_SECONDS = 60
# Hard ceiling on decoded raster size (a pixel-flood/decompression bomb can
# smuggle a huge raster through the byte-size cap). Applied both to Pillow's
# own bomb detector (MAX_IMAGE_PIXELS) and to an explicit pre-`convert` check.
_MAX_IMAGE_PIXELS = 40_000_000

# Fail closed on a decompression bomb: cap Pillow's raster allocation
# process-wide so `Image.open`/`load` raises `DecompressionBombWarning` (which
# `fetch_image_safely` promotes to a hard error) instead of silently blowing up
# a small compressed image into a multi-GB raster.
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS


@dataclass(frozen=True)
class ImagePayload:
    url: str
    org_id: str
    chat_id: str
    message_id: str
    exp: int


def _signing_key(settings: Settings) -> bytes:
    """Derived once per call from the KEK -- never the raw KEK itself (see
    module docstring). Cheap enough (one HMAC) to not bother caching."""
    return hmac.new(load_kek(settings.kek_file), _SIGNING_CONTEXT, hashlib.sha256).digest()


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_nopad_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _now() -> int:
    return int(time.time())


def mint_image_ref(
    url: str,
    *,
    org_id: UUID,
    chat_id: UUID,
    message_id: UUID,
    settings: Settings,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    now: int,
) -> str:
    payload = {
        "u": url,
        "o": str(org_id),
        "c": str(chat_id),
        "m": str(message_id),
        "exp": now + ttl_seconds,
    }
    body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_signing_key(settings), body_bytes, hashlib.sha256).digest()
    return f"{_b64url_nopad(body_bytes)}.{_b64url_nopad(sig)}"


def verify_image_ref(ref: str, *, settings: Settings, now: int) -> ImagePayload | None:
    """Constant-time verify + expiry check. NEVER raises -- any malformed
    input, decode failure, or signature mismatch is just "not a valid ref"
    (a 404, not a 500) since `ref` is fully attacker-controlled on this
    public route."""
    try:
        body, sig = ref.split(".", 1)
        body_bytes = _b64url_nopad_decode(body)
        sig_bytes = _b64url_nopad_decode(sig)
        expected_sig = hmac.new(_signing_key(settings), body_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(sig_bytes, expected_sig):
            return None
        data: dict[str, Any] = json.loads(body_bytes)
        exp = int(data["exp"])
        if exp < now:
            return None
        return ImagePayload(
            url=str(data["u"]),
            org_id=str(data["o"]),
            chat_id=str(data["c"]),
            message_id=str(data["m"]),
            exp=exp,
        )
    except Exception:  # noqa: BLE001 - attacker-controlled input, fail closed to None
        return None


def _ip_is_bad(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_safe(host: str) -> list[str] | None:
    """SSRF guard + DNS-rebind pin source. Resolve `host` ONCE and return the
    list of validated literal IPs, or None if `host` is empty, fails to
    resolve, resolves to no addresses, or ANY resolved address is
    private/loopback/link-local/reserved/multicast/unspecified. Returning the
    *validated* IPs (rather than a bool) lets the caller pin the socket to an
    address it has already checked, closing the resolve-here/connect-there
    (DNS-rebind TOCTOU) gap -- httpx never re-resolves, it dials the literal IP.

    Because ANY bad address in the answer set rejects the whole host, a domain
    that returns both a public and a private/link-local A/AAAA record (the
    classic "answer public to the guard, private to the connector" split) is
    rejected outright.

    Fail closed on every ambiguous case -- mirrors `core/net.py::is_blocked_ip`,
    but sync and unconditional (no env gate): image fetches are
    attacker-influenceable in every environment.

    Fix 4 (CPython 3.12.0-3.12.3 misclassified `::ffff:x.y.z.w`): for every
    IPv4-mapped IPv6 address also run the same checks against the embedded IPv4,
    blocking if either the v6 form or the mapped v4 is bad.
    """
    if not host:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None
    if not infos:
        return None
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0])
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if _ip_is_bad(addr):
            return None
        mapped = getattr(addr, "ipv4_mapped", None)
        if mapped is not None and _ip_is_bad(mapped):
            return None
        ips.append(ip)
    return ips


def _host_is_blocked(host: str) -> bool:
    """Back-compat boolean form of `_resolve_safe` (True == blocked). The
    `_host_is_blocked`-before-any-network-call guarantee is preserved: both are
    the same one resolution + validation path."""
    return _resolve_safe(host) is None


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """Production-only DNS-rebind pin. Connects the socket to an
    already-validated literal IP while keeping the original hostname for the
    `Host` header and the TLS SNI / certificate check (httpcore's
    `sni_hostname` extension) -- so the pin does NOT weaken TLS. Used only when
    no `transport` is injected; the test path injects its own transport (and
    monkeypatches getaddrinfo), so it skips the socket-level pin."""

    def __init__(self, ip: str, hostname: str) -> None:
        super().__init__()
        self._pinned_ip = ip
        self._hostname = hostname

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Dial the pinned IP; the Host header (set at request build from the
        # original hostname) is left intact and SNI is forced to the hostname.
        request.url = request.url.copy_with(host=self._pinned_ip)
        request.extensions = {**request.extensions, "sni_hostname": self._hostname}
        return await super().handle_async_request(request)


def _pin_transport(
    transport: httpx.AsyncBaseTransport | None, ips: list[str], hostname: str
) -> httpx.AsyncBaseTransport:
    if transport is not None:
        # Injected transport (tests) -- do not wrap; the caller controls dialing.
        return transport
    return _PinnedTransport(ips[0], hostname)


async def _read_capped_body(response: httpx.Response, max_bytes: int) -> bytes | None:
    """Status/content-type/size gate + STREAMING read. All header checks run
    BEFORE the body is consumed; the body is accumulated chunk-by-chunk and
    aborted the instant it exceeds `max_bytes`, so a `content-length`-omitting
    host that streams gigabytes can never buffer past the cap."""
    if response.status_code != 200:
        return None
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("image/"):
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
    return bytes(buf)


async def _stream_get(
    url: str,
    *,
    hostname: str,
    ips: list[str],
    transport: httpx.AsyncBaseTransport | None,
    timeout: httpx.Timeout,
    max_bytes: int,
) -> tuple[bytes | None, str | None] | None:
    """One streaming GET against a validated+pinned host. Returns
    `(body, None)` on a 200 image within cap, `(None, location)` on a redirect
    with a Location header, or None on any hard failure."""
    client_transport = _pin_transport(transport, ips, hostname)
    async with httpx.AsyncClient(
        transport=client_transport, timeout=timeout, follow_redirects=False
    ) as client:
        async with client.stream("GET", url) as response:
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("location")
                if not location:
                    return None
                return (None, location)
            body = await _read_capped_body(response, max_bytes)
            if body is None:
                return None
            return (body, None)


async def fetch_image_safely(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: httpx.Timeout | None = None,
) -> tuple[bytes, str] | None:
    """Fetch `url` under a full SSRF + content guard and re-encode to WEBP
    to strip metadata (EXIF etc.). Never raises -- any guard failure or
    malformed response is just None, and the caller (the public proxy
    route) turns that into a plain 404."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    ips = _resolve_safe(parsed.hostname)
    if ips is None:
        return None

    client_timeout = timeout or httpx.Timeout(20.0, connect=5.0)
    try:
        first = await _stream_get(
            url,
            hostname=parsed.hostname,
            ips=ips,
            transport=transport,
            timeout=client_timeout,
            max_bytes=max_bytes,
        )
        if first is None:
            return None
        content, location = first
        if content is None:
            # Redirect: re-validate + re-resolve (fresh pin) the target host
            # before the one permitted follow-up, same streaming gate.
            if location is None:
                return None
            redirect_url = urljoin(url, location)
            redirect_parsed = urlparse(redirect_url)
            if redirect_parsed.scheme not in ("http", "https") or not redirect_parsed.hostname:
                return None
            redirect_ips = _resolve_safe(redirect_parsed.hostname)
            if redirect_ips is None:
                return None
            second = await _stream_get(
                redirect_url,
                hostname=redirect_parsed.hostname,
                ips=redirect_ips,
                transport=transport,
                timeout=client_timeout,
                max_bytes=max_bytes,
            )
            if second is None:
                return None
            content, _location2 = second
            # A second redirect is not followed.
            if content is None:
                return None
    except httpx.HTTPError:
        return None

    try:
        # Promote Pillow's decompression-bomb warning to a hard error so a
        # small compressed image that expands to a huge raster fails closed.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            opened = Image.open(BytesIO(content))
            if opened.size[0] * opened.size[1] > _MAX_IMAGE_PIXELS:
                return None
            rgb = opened.convert("RGB")
            buf = BytesIO()
            rgb.save(buf, format="WEBP")
            return buf.getvalue(), "image/webp"
    except Exception:  # noqa: BLE001 - malformed/hostile image bytes, fail closed to None
        return None


async def get_or_fetch_image(
    ref: str,
    *,
    redis: Any,
    settings: Settings,
    now: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str] | None:
    payload = verify_image_ref(ref, settings=settings, now=now)
    if payload is None:
        return None

    cache_key = f"imgproxy:v1:{hashlib.sha256(ref.encode()).hexdigest()}"
    if redis is not None:
        cached = await redis.get(cache_key)
        if cached is not None:
            # Empty value == negative marker: a prior fetch failed. Serve the
            # 404 straight from cache instead of re-driving the upstream fetch
            # (SSRF-probe / amplification hardening). Non-empty == the webp.
            if len(cached) == 0:
                return None
            return bytes(cached), "image/webp"

    fetched = await fetch_image_safely(payload.url, transport=transport)
    if fetched is None:
        if redis is not None:
            await redis.set(cache_key, b"", ex=_NEG_CACHE_TTL_SECONDS)
        return None

    if redis is not None:
        await redis.set(cache_key, fetched[0], ex=_CACHE_TTL_SECONDS)

    return fetched
