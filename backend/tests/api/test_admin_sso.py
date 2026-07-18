import httpx

from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_sso_config_roundtrip_never_redisplays_secret(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/admin/sso", headers=h)
    assert r.json() == {"issuer": None, "client_id": None, "client_secret_set": False}

    r = await client.put("/api/v1/admin/sso", headers=h, json={
        "issuer": "https://idp.example.com", "client_id": "raghub",
        "client_secret": "s3cret-value",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["client_secret_set"] is True
    assert "s3cret-value" not in r.text  # SEC-2: write-only

    # PUT without a secret keeps the stored one
    r = await client.put("/api/v1/admin/sso", headers=h, json={
        "issuer": "https://idp.example.com", "client_id": "raghub2",
    })
    assert r.json() == {"issuer": "https://idp.example.com", "client_id": "raghub2",
                        "client_secret_set": True}


async def test_org_sso_domains(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    orgs = (await client.get("/api/v1/admin/orgs", headers=h)).json()
    org_id = orgs[0]["id"]
    r = await client.put(f"/api/v1/admin/orgs/{org_id}/sso-domains",
                         headers=h, json={"domains": ["Acme.COM", "acme.io"]})
    assert r.status_code == 200
    assert r.json()["sso_domains"] == ["acme.com", "acme.io"]  # normalized lowercase


async def test_sso_admin_requires_superadmin(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")  # org admin, not superadmin
    assert (await client.get("/api/v1/admin/sso", headers=h)).status_code == 403
