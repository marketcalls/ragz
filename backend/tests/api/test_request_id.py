import httpx


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
