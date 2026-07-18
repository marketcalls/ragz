import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def seed_events(session: AsyncSession, seeded_user: User, n: int = 7) -> None:
    for i in range(n):
        await record_audit(session, org_id=seeded_user.org_id, actor_id=seeded_user.id,
                           action=f"test.event_{i}", target_type="test", target_id=str(i))
    await session.commit()


async def test_keyset_pagination_walks_everything_once(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    await seed_events(session, seeded_user, n=7)
    h = await auth(client, "root@platform.example")
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = "/api/v1/admin/audit?limit=3&action=test"
        if cursor:
            url += f"&cursor={cursor}"
        body = (await client.get(url, headers=h)).json()
        seen += [e["target_id"] for e in body["events"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 7
    assert len(set(seen)) == 7  # no duplicates across page boundaries
    assert seen == sorted(seen, key=int, reverse=True)  # newest first


async def test_filters(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    await seed_events(session, seeded_user, n=3)
    await record_audit(session, org_id=None, actor_id=None, action="other.thing",
                       target_type="x", target_id="x")
    await session.commit()
    h = await auth(client, "root@platform.example")
    body = (
        await client.get(f"/api/v1/admin/audit?org_id={seeded_user.org_id}&action=test",
                         headers=h)
    ).json()
    assert len(body["events"]) == 3
    body = (
        await client.get(f"/api/v1/admin/audit?actor_id={seeded_user.id}", headers=h)
    ).json()
    assert all(e["actor_id"] == str(seeded_user.id) for e in body["events"])


async def test_audit_viewer_requires_superadmin(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    assert (await client.get("/api/v1/admin/audit", headers=h)).status_code == 403
