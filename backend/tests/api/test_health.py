import httpx


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readyz(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
