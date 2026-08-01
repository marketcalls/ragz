import httpx

from ragz.modules.auth.models import User


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


async def test_delete_secret(client: httpx.AsyncClient, superadmin_headers: dict[str, str]) -> None:
    await client.put("/api/v1/admin/secrets/smtp_password",
                     json={"value": "x"}, headers=superadmin_headers)
    r = await client.delete("/api/v1/admin/secrets/smtp_password", headers=superadmin_headers)
    assert r.status_code == 204
    names = [s["name"] for s in (
        await client.get("/api/v1/admin/secrets", headers=superadmin_headers)
    ).json()]
    assert "smtp_password" not in names


async def test_delete_missing_404(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    assert (await client.delete("/api/v1/admin/secrets/nope",
                                headers=superadmin_headers)).status_code == 404


async def test_bad_name_rejected(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    r = await client.put("/api/v1/admin/secrets/bad%2Fname",
                         json={"value": "x"}, headers=superadmin_headers)
    assert r.status_code == 422
