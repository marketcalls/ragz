import httpx

from ragz.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_get_settings_defaults(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/admin/settings", headers=superadmin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["document_parser"] == "liteparse"
    assert body["rerank_provider"] == "local"
    assert body["llamaparse_key_set"] is False
    # never a key value in the payload
    assert "llamaparse_api_key" not in body and "cohere_api_key" not in body


async def test_put_settings_stores_and_masks_keys(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    r = await client.put(
        "/api/v1/admin/settings", headers=superadmin_headers,
        json={"rerank_provider": "cohere", "cohere_api_key": "ck-live"},
    )
    assert r.status_code == 200
    assert r.json()["rerank_provider"] == "cohere"
    assert r.json()["cohere_key_set"] is True
    assert "cohere_api_key" not in r.json()


async def test_settings_requires_superadmin(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")  # role=admin, not superadmin
    assert (await client.get("/api/v1/admin/settings", headers=h)).status_code == 403
    r = await client.put("/api/v1/admin/settings", json={"rerank_provider": "cohere"}, headers=h)
    assert r.status_code == 403
