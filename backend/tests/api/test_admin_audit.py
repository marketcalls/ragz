from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.audit.models import AuditEvent
from ragz.modules.audit.service import record_audit
from ragz.modules.auth.models import User


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


async def test_action_prefix_wildcard_is_escaped(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    """`action=%` must only match actions that literally start with "%" --
    not every row -- and embedded `%`/`_` must not widen the match either."""
    await seed_events(session, seeded_user, n=3)
    h = await auth(client, "root@platform.example")

    body = (
        await client.get("/api/v1/admin/audit", params={"action": "%"}, headers=h)
    ).json()
    assert body["events"] == []

    # "_" is a single-char LIKE wildcard; "t_st" must not match "test.event_*".
    body = (
        await client.get("/api/v1/admin/audit", params={"action": "t_st"}, headers=h)
    ).json()
    assert body["events"] == []


async def test_date_from_normalizes_tz_aware_query_param(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    """A tz-aware ISO `date_from` (as browsers/JS `toISOString()` send it) must
    filter against the same UTC instant as the naive-UTC `created_at` column,
    not 500 at the asyncpg layer and not silently shift by the offset."""
    await record_audit(session, org_id=seeded_user.org_id, actor_id=seeded_user.id,
                       action="test.tz_check", target_type="test", target_id="tz")
    await session.commit()
    row = (
        await session.execute(select(AuditEvent).where(AuditEvent.action == "test.tz_check"))
    ).scalar_one()
    created_at = row.created_at  # naive UTC, as stored

    # Same instant as `created_at`, but expressed with a +05:30 wall-clock offset.
    # A buggy implementation that strips tzinfo without converting to UTC first
    # would treat this as 5.5 hours later and wrongly exclude the row below.
    aware_equivalent = (created_at + timedelta(hours=5, minutes=30)).isoformat() + "+05:30"
    h = await auth(client, "root@platform.example")
    resp = await client.get(
        "/api/v1/admin/audit",
        params={"action": "test.tz_check", "date_from": aware_equivalent},
        headers=h,
    )
    assert resp.status_code == 200
    assert [e["target_id"] for e in resp.json()["events"]] == ["tz"]

    # "Z"-suffixed UTC form must also succeed (not 500 against the naive column).
    z_form = created_at.isoformat() + "Z"
    resp = await client.get(
        "/api/v1/admin/audit",
        params={"action": "test.tz_check", "date_from": z_form},
        headers=h,
    )
    assert resp.status_code == 200
    assert [e["target_id"] for e in resp.json()["events"]] == ["tz"]
