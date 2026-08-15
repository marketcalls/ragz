"""Signed capability URLs for proxied images (openui-parity Task 7).

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


def _host_is_blocked(host: str) -> bool:
    """SSRF guard: True iff `host` is empty, fails to resolve, or resolves
    to ANY private/loopback/link-local/reserved/multicast/unspecified
    address. Fail closed on every ambiguous case (no addresses, resolution
    error) -- mirrors `core/net.py::is_blocked_ip`'s posture, but this is a
    sync, unconditional (no environment gate) check: image fetches are
    attacker-influenceable in every environment, not just prod/staging."""
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    if not infos:
        return True
    for info in infos:
        sockaddr = info[4]
        ip = str(sockaddr[0])
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return True
    return False


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
    if _host_is_blocked(parsed.hostname):
        return None

    client_timeout = timeout or httpx.Timeout(20.0, connect=5.0)
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=client_timeout, follow_redirects=False
        ) as client:
            response = await client.get(url)
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("location")
                if not location:
                    return None
                redirect_url = urljoin(url, location)
                redirect_parsed = urlparse(redirect_url)
                if redirect_parsed.scheme not in ("http", "https") or not redirect_parsed.hostname:
                    return None
                if _host_is_blocked(redirect_parsed.hostname):
                    return None
                response = await client.get(redirect_url)

            if response.status_code != 200:
                return None

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        return None
                except ValueError:
                    return None

            content = response.content
            if len(content) > max_bytes:
                return None

            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("image/"):
                return None
    except httpx.HTTPError:
        return None

    try:
        opened = Image.open(BytesIO(content))
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
            return bytes(cached), "image/webp"

    fetched = await fetch_image_safely(payload.url, transport=transport)
    if fetched is None:
        return None

    if redis is not None:
        await redis.set(cache_key, fetched[0], ex=_CACHE_TTL_SECONDS)

    return fetched
