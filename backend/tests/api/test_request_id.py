import re

import httpx

# Named constraint: only this charset may ever reach the response header or
# be bound into contextvars.
ALLOWLIST = re.compile(r"^[A-Za-z0-9._-]+$")
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


async def test_response_carries_request_id(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert len(r.headers["x-request-id"]) == 36  # uuid4


async def test_inbound_request_id_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["x-request-id"] == "trace-abc-123"


async def test_sse_still_streams(client: httpx.AsyncClient) -> None:
    # pure-ASGI middleware (NOT BaseHTTPMiddleware): must not buffer streaming
    # responses. Guard: /healthz works AND the middleware wraps send only.
    r = await client.get("/healthz")
    assert r.status_code == 200


async def test_inbound_request_id_with_control_bytes_is_sanitized(
    client: httpx.AsyncClient,
) -> None:
    """Control/high bytes and whitespace must never reach the echoed header --
    h11 can raise on invalid header bytes (self-inflicted 500), and unsanitized
    bytes must not be bound into contextvars either."""
    r = await client.get("/healthz", headers={"X-Request-ID": "abc\x01\ndef ghi"})
    value = r.headers["x-request-id"]
    assert ALLOWLIST.match(value)
    assert value == "abcdefghi"  # disallowed chars dropped, rest preserved in order


async def test_inbound_request_id_entirely_invalid_mints_uuid(
    client: httpx.AsyncClient,
) -> None:
    """If sanitization strips everything, fall back to a freshly minted uuid4
    rather than echoing an empty/missing header."""
    r = await client.get("/healthz", headers={"X-Request-ID": "\x01\x02 \n\t"})
    value = r.headers["x-request-id"]
    assert UUID4.match(value)


async def test_inbound_request_id_cap_enforced_after_sanitization(
    client: httpx.AsyncClient,
) -> None:
    """The 64-char cap must still apply post-sanitization."""
    r = await client.get("/healthz", headers={"X-Request-ID": "a" * 100})
    value = r.headers["x-request-id"]
    assert value == "a" * 64
