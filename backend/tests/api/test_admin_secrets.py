import httpx

from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_write_and_list_never_expose_value(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.put("/api/v1/admin/secrets/openai_key",
                         json={"value": "sk-verysecret-abcd"}, headers=h)
    assert r.status_code == 200
    assert "sk-verysecret" not in r.text  # write-only: only ...abcd + hash may appear
    assert r.json()["fingerprint"].startswith("...abcd sha256:")

    r2 = await client.get("/api/v1/admin/secrets", headers=h)
    assert r2.status_code == 200
    assert "sk-verysecret" not in r2.text
    assert [s["name"] for s in r2.json()] == ["openai_key"]
    assert r2.json()[0]["last_used_at"] is None


async def test_admin_role_is_denied(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")  # role=admin, not superadmin
    assert (await client.get("/api/v1/admin/secrets", headers=h)).status_code == 403
    r = await client.put("/api/v1/admin/secrets/x", json={"value": "v"}, headers=h)
    assert r.status_code == 403
