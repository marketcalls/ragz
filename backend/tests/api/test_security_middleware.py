"""RAGZ-PUB-09 (security headers + docs gating), RAGZ-PUB-03 (global
body-size limit), RAGZ-PUB-06 (TrustedHost) -- edge middleware hardening.

Header/docs-gating/TrustedHost tests build `create_app(settings=...)`
directly and only ever hit `/healthz`, which touches neither the database
nor Redis -- `build_engine`/`Redis.from_url` are lazy, so this needs no
testcontainers, unlike most of the API test suite. The body-size-limit
tests drive `BodySizeLimitMiddleware` directly against a trivial ASGI app
so the oversized-body case doesn't require allocating hundreds of MB.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from ragz.api.app import create_app
from ragz.api.security_middleware import (
    BodySizeLimitMiddleware,
    body_size_ceiling_bytes,
    trusted_hosts_for,
)
from ragz.core.config import Settings

# Mirrors tests/core/test_production_config.py's _SAFE_PRODUCTION_KWARGS:
# every field Settings._production_fails_closed checks, overridden away
# from its insecure dev default, so constructing this Settings doesn't
# raise.
_SAFE_PRODUCTION_KWARGS: dict[str, object] = {
    "_env_file": None,
    "environment": "production",
    "api_key_pepper": "a-real-random-pepper-value",
    "database_url": "postgresql+asyncpg://ragz_prod:s3cret-pw@db.internal:5432/ragz",
    "minio_secret_key": "a-real-minio-secret",
    "litellm_master_key": "sk-a-real-litellm-master-key",
    "public_api_base_url": "https://api.example.com",
    "frontend_base_url": "https://app.example.com",
    "kek_file": "/etc/ragz/kek",
}


@pytest.fixture
def test_settings() -> Settings:
    return Settings(_env_file=None, environment="test")


@pytest.fixture
def production_settings() -> Settings:
    return Settings(**_SAFE_PRODUCTION_KWARGS)  # type: ignore[arg-type]


@pytest.fixture
async def bare_client(test_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A `client`-fixture lookalike, but built without testcontainers --
    session_factory/redis_client are left at create_app's lazy defaults,
    which never connect anywhere as long as the test only hits routes
    (like /healthz) that don't touch either."""
    app = create_app(settings=test_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Security response headers (RAGZ-PUB-09) --------------------------------


async def test_security_headers_present_on_every_response(
    bare_client: httpx.AsyncClient,
) -> None:
    r = await bare_client.get("/healthz")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert r.headers["permissions-policy"] == "geolocation=(), microphone=(), camera=()"


async def test_hsts_absent_in_test_environment(bare_client: httpx.AsyncClient) -> None:
    r = await bare_client.get("/healthz")
    assert "strict-transport-security" not in r.headers


async def test_hsts_present_in_production(production_settings: Settings) -> None:
    app = create_app(settings=production_settings)
    # TrustedHostMiddleware (RAGZ-PUB-06) only allows the production host
    # derived from public_api_base_url, so the test client's Host header
    # must match it.
    transport = httpx.ASGITransport(app=app, client=("api.example.com", 443))
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.com") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


# --- API docs gating (RAGZ-PUB-09) ------------------------------------------


async def test_docs_reachable_in_test_environment(bare_client: httpx.AsyncClient) -> None:
    r = await bare_client.get("/api/docs")
    assert r.status_code == 200
    r2 = await bare_client.get("/api/openapi.json")
    assert r2.status_code == 200


def test_docs_disabled_in_production(production_settings: Settings) -> None:
    app = create_app(settings=production_settings)
    assert app.docs_url is None
    assert app.openapi_url is None


# --- TrustedHost (RAGZ-PUB-06) ----------------------------------------------


def test_trusted_hosts_permissive_outside_production() -> None:
    assert trusted_hosts_for("dev", "http://localhost:8000") == ["*"]
    assert trusted_hosts_for("test", "http://localhost:8000") == ["*"]
    assert trusted_hosts_for("staging", "https://staging.example.com") == ["*"]


def test_trusted_hosts_derived_from_public_api_base_url_in_production() -> None:
    assert trusted_hosts_for("production", "https://api.example.com") == ["api.example.com"]


async def test_mismatched_host_rejected_in_production(production_settings: Settings) -> None:
    app = create_app(settings=production_settings)
    transport = httpx.ASGITransport(app=app, client=("evil.example.com", 443))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://evil.example.com"
    ) as c:
        r = await c.get("/healthz")
    assert r.status_code == 400


# --- Global body-size limit (RAGZ-PUB-03) -----------------------------------


def test_body_size_ceiling_is_above_max_upload_mb() -> None:
    ceiling = body_size_ceiling_bytes(max_upload_mb=100)
    assert ceiling > 100 * 1024 * 1024
    # Floor kicks in for a very low max_upload_mb so ordinary JSON traffic
    # still has a sane global ceiling.
    assert body_size_ceiling_bytes(max_upload_mb=1) >= 25 * 1024 * 1024


async def _echo_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    body = b""
    more_body = True
    while more_body:
        message = await receive()
        body += message.get("body", b"")
        more_body = message.get("more_body", False)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": f"received {len(body)}".encode()})


@pytest.fixture
async def size_limited_client() -> AsyncIterator[httpx.AsyncClient]:
    wrapped = BodySizeLimitMiddleware(_echo_app, max_bytes=16)
    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_body_within_limit_passes_through(size_limited_client: httpx.AsyncClient) -> None:
    r = await size_limited_client.post("/anything", content=b"x" * 10)
    assert r.status_code == 200
    assert r.text == "received 10"


async def test_declared_content_length_over_limit_rejected(
    size_limited_client: httpx.AsyncClient,
) -> None:
    r = await size_limited_client.post("/anything", content=b"x" * 100)
    assert r.status_code == 413
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["status"] == 413
    assert "exceeds" in body["detail"]


async def test_chunked_body_over_limit_rejected_without_content_length(
    size_limited_client: httpx.AsyncClient,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 10  # 50 bytes total > the 16-byte ceiling

    r = await size_limited_client.post("/anything", content=chunks())
    assert "content-length" not in r.request.headers
    assert r.status_code == 413


async def test_chunked_body_within_limit_passes_through(
    size_limited_client: httpx.AsyncClient,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"x" * 5  # 15 bytes total, under the 16-byte ceiling

    r = await size_limited_client.post("/anything", content=chunks())
    assert r.status_code == 200
    assert r.text == "received 15"
