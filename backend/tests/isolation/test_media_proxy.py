"""Isolation/security tests for the SSRF-guarded image proxy + signed
image_ref (openui-parity Task 7).

The proxy is a CAPABILITY URL: the ref itself is the authorization (HMAC-signed
with a KEK-derived key + an expiry inside the payload). The route is PUBLIC (no
TenantContext) -- a valid, unexpired signature is the sole gate, mirroring a
signed-S3-URL design. These tests pin:
  * the signing round-trip + tamper/expiry rejection (unforgeable, time-limited);
  * the SSRF guard (`_host_is_blocked` rejects private/loopback/link-local/etc.);
  * `fetch_image_safely`'s content-type / size / private-host guards + re-encode;
  * the public route returns normalized webp (200) or 404, with the safe headers.
"""

import socket
import time
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.chat import media


def _png_bytes(
    size: tuple[int, int] = (3, 3), color: tuple[int, int, int] = (200, 30, 30)
) -> bytes:
    img = Image.new("RGB", size, color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _addrinfo(ip: str) -> list[tuple]:
    """Shape of socket.getaddrinfo() output: (family, type, proto, canon, sockaddr)."""
    if ":" in ip:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


# --------------------------------------------------------------------------
# mint / verify (capability token)
# --------------------------------------------------------------------------


def test_mint_verify_round_trip(test_settings: Settings) -> None:
    org_id, chat_id, message_id = uuid4(), uuid4(), uuid4()
    now = 1_000_000
    ref = media.mint_image_ref(
        "https://cdn.example.com/pic.jpg",
        org_id=org_id,
        chat_id=chat_id,
        message_id=message_id,
        settings=test_settings,
        now=now,
    )
    payload = media.verify_image_ref(ref, settings=test_settings, now=now)
    assert payload is not None
    assert payload.url == "https://cdn.example.com/pic.jpg"
    assert payload.org_id == str(org_id)
    assert payload.chat_id == str(chat_id)
    assert payload.message_id == str(message_id)
    assert payload.exp == now + 86400


def test_verify_rejects_tampered_signature(test_settings: Settings) -> None:
    ref = media.mint_image_ref(
        "https://cdn.example.com/pic.jpg",
        org_id=uuid4(), chat_id=uuid4(), message_id=uuid4(),
        settings=test_settings, now=1_000_000,
    )
    body, sig = ref.split(".", 1)
    # Flip a character in the signature part.
    flipped = "A" if sig[0] != "A" else "B"
    tampered = f"{body}.{flipped}{sig[1:]}"
    assert media.verify_image_ref(tampered, settings=test_settings, now=1_000_000) is None


def test_verify_rejects_expired(test_settings: Settings) -> None:
    now = 1_000_000
    ref = media.mint_image_ref(
        "https://cdn.example.com/pic.jpg",
        org_id=uuid4(), chat_id=uuid4(), message_id=uuid4(),
        settings=test_settings, now=now, ttl_seconds=10,
    )
    # A moment before expiry still verifies; one second past exp does not.
    assert media.verify_image_ref(ref, settings=test_settings, now=now + 10) is not None
    assert media.verify_image_ref(ref, settings=test_settings, now=now + 11) is None


def test_verify_rejects_garbage(test_settings: Settings) -> None:
    for junk in ["", "not-a-ref", "a.b.c", "@@@.$$$", "onlyonepart"]:
        assert media.verify_image_ref(junk, settings=test_settings, now=1) is None


def test_ref_carries_minting_org_capability_model(test_settings: Settings) -> None:
    """The capability model does NOT cross-check TenantContext (the route is
    public); the ref binds the minting org_id purely for auditability. A ref
    minted for org A verifies structurally regardless of any caller ctx."""
    org_a = uuid4()
    ref = media.mint_image_ref(
        "https://cdn.example.com/a.jpg",
        org_id=org_a, chat_id=uuid4(), message_id=uuid4(),
        settings=test_settings, now=1_000_000,
    )
    payload = media.verify_image_ref(ref, settings=test_settings, now=1_000_000)
    assert payload is not None
    assert payload.org_id == str(org_a)  # auditable minting org travels in the token


# --------------------------------------------------------------------------
# SSRF guard: _host_is_blocked
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "blocked"),
    [
        ("127.0.0.1", True),     # loopback
        ("10.0.0.5", True),      # private
        ("169.254.1.1", True),   # link-local
        ("::1", True),           # loopback v6
        ("0.0.0.0", True),  # noqa: S104 — test data (unspecified addr), not a bind
        ("93.184.216.34", False),  # public
    ],
)
def test_host_is_blocked(monkeypatch: pytest.MonkeyPatch, ip: str, blocked: bool) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip))
    assert media._host_is_blocked("host.example") is blocked


def test_host_is_blocked_on_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> object:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert media._host_is_blocked("nx.example") is True


def test_host_is_blocked_empty_host() -> None:
    assert media._host_is_blocked("") is True


# --------------------------------------------------------------------------
# fetch_image_safely
# --------------------------------------------------------------------------


async def test_fetch_rejects_non_image_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nope</html>",
                              headers={"content-type": "text/html"})

    out = await media.fetch_image_safely(
        "https://pub.example.com/x", transport=httpx.MockTransport(handler)
    )
    assert out is None


async def test_fetch_rejects_oversize_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"x" * 10,
            headers={"content-type": "image/png", "content-length": "9999999"},
        )

    out = await media.fetch_image_safely(
        "https://pub.example.com/big.png", transport=httpx.MockTransport(handler), max_bytes=1000
    )
    assert out is None


async def test_fetch_rejects_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("10.1.2.3"))
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    out = await media.fetch_image_safely(
        "https://internal.example.com/x.png", transport=httpx.MockTransport(handler)
    )
    assert out is None
    assert called["n"] == 0  # blocked BEFORE any network call


async def test_fetch_rejects_non_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    out = await media.fetch_image_safely("file:///etc/passwd")
    assert out is None


async def test_fetch_normalizes_png_to_webp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    png = _png_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    out = await media.fetch_image_safely(
        "https://pub.example.com/pic.png", transport=httpx.MockTransport(handler)
    )
    assert out is not None
    data, content_type = out
    assert content_type == "image/webp"
    # Re-encoded bytes must be a valid image Pillow can re-open, and not the
    # original PNG (metadata/EXIF stripped via convert->save).
    reopened = Image.open(BytesIO(data))
    assert reopened.format == "WEBP"
    assert data != png


# --------------------------------------------------------------------------
# Route: GET /api/v1/media/image/{ref}
# --------------------------------------------------------------------------


@pytest.fixture
async def media_client(engine, redis_client, test_settings: Settings):  # type: ignore[no-untyped-def]
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings

    def _png_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    app.state.image_transport = httpx.MockTransport(_png_handler)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, test_settings


async def test_route_valid_ref_returns_webp(
    media_client, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    client, settings = media_client
    ref = media.mint_image_ref(
        "https://pub.example.com/pic.png",
        org_id=uuid4(), chat_id=uuid4(), message_id=uuid4(),
        settings=settings, now=int(time.time()),
    )
    resp = await client.get(f"/api/v1/media/image/{ref}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["x-content-type-options"] == "nosniff"
    Image.open(BytesIO(resp.content))  # decodes


async def test_route_garbage_ref_returns_404(media_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = media_client
    resp = await client.get("/api/v1/media/image/totally-bogus-ref")
    assert resp.status_code == 404
