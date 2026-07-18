import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.quotas.service import record_usage


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_usage_me(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    h_super = await auth(client, "root@platform.example")
    await client.put(f"/api/v1/admin/orgs/{seeded_user.org_id}/quota", headers=h_super,
                     json={"monthly_tokens": 100_000, "default_user_monthly_tokens": 1_000,
                           "reset_day": 1})
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=800, completion_tokens=100)
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/usage/me", headers=h)
    body = r.json()
    assert body["used_tokens"] == 900
    assert body["allocated_tokens"] == 1_000
    assert body["warning"] is True
    assert body["resets_at"]


async def test_admin_summary_org_scoped(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    from uuid import uuid4

    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=10, completion_tokens=5)
    # foreign-org noise must never appear in this org's summary
    await record_usage(session, org_id=uuid4(), user_id=uuid4(),
                       model_id=None, feature="chat", prompt_tokens=999, completion_tokens=0)
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/admin/usage/summary?days=30", headers=h)
    body = r.json()
    assert sum(d["tokens"] for d in body["by_day"]) == 15
    assert body["by_user"] == [
        {"user_id": str(seeded_user.id), "email": "a@acme.com", "tokens": 15}
    ]


async def test_platform_usage_requires_superadmin(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=10, completion_tokens=0)
    h_admin = await auth(client, "a@acme.com")
    assert (await client.get("/api/v1/admin/usage/orgs", headers=h_admin)).status_code == 403
    h_super = await auth(client, "root@platform.example")
    rows = (await client.get("/api/v1/admin/usage/orgs", headers=h_super)).json()
    assert {"org_id": str(seeded_user.org_id), "name": "Acme", "tokens": 10} in rows
