from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.audit.models import AuditEvent
from ragz.modules.audit.service import record_audit
from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Organization, RoleTemplate


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def seed_events(session: AsyncSession, seeded_user: User, n: int = 7) -> None:
    for i in range(n):
        await record_audit(session, org_id=seeded_user.org_id, actor_id=seeded_user.id,
                           action=f"test.event_{i}", target_type="test", target_id=str(i))
    await session.commit()


async def make_org_scoped_audit_reader(
    session: AsyncSession, seeded_user: User, *, email: str = "audit-reader@acme.com"
) -> User:
    """RBAC-05: a role="user" account (any tier short of superadmin could hold
    this) carrying ONLY the audit.read/audit.export grant -- mirrors the seeded
    "Audit Reader" template (id 00000000-0000-0000-0000-000000000c07) without
    depending on migrations having run against the test schema (tests build
    schema via Base.metadata.create_all, not Alembic)."""
    template = RoleTemplate(name=f"Audit Reader Test {email}",
                            permissions=["audit.read", "audit.export"])
    session.add(template)
    await session.flush()
    user = User(org_id=seeded_user.org_id, email=email,
                password_hash=seeded_user.password_hash, role="user",
                custom_role_id=template.id)
    session.add(user)
    await session.commit()
    return user


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


async def test_org_scoped_audit_reader_sees_only_own_org(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    """RBAC-05: an org-scoped Audit-Reader-templated account (role="user" +
    audit.read/audit.export grant) attempting to widen scope via ?org_id=
    still only ever sees ITS OWN org's events -- the query param is
    ignored/overridden, never trusted."""
    await seed_events(session, seeded_user, n=3)
    rival_org = Organization(name="Rival")
    session.add(rival_org)
    await session.flush()
    await record_audit(session, org_id=rival_org.id, actor_id=None,
                       action="test.rival", target_type="test", target_id="riv")
    await session.commit()

    await make_org_scoped_audit_reader(session, seeded_user)
    h = await auth(client, "audit-reader@acme.com")

    r = await client.get(
        "/api/v1/admin/audit", headers=h, params={"org_id": str(rival_org.id)},
    )
    assert r.status_code == 200
    events = r.json()["events"]
    assert events  # non-empty -- proves the override widened to ITS OWN org,
    # not that it silently returned nothing.
    assert all(e["org_id"] == str(seeded_user.org_id) for e in events)
    assert all(e["org_id"] != str(rival_org.id) for e in events)


async def test_audit_reader_cannot_administer(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    """RBAC-05 separation of duties (NIST AC-5): holding audit.read does NOT
    imply any IAM/administration capability."""
    await make_org_scoped_audit_reader(session, seeded_user, email="audit-reader2@acme.com")
    h = await auth(client, "audit-reader2@acme.com")

    r = await client.get("/api/v1/users", headers=h)
    assert r.status_code == 403
    r2 = await client.post(
        "/api/v1/admin/roles", headers=h, json={"name": "x", "permissions": []}
    )
    assert r2.status_code == 403


async def test_admin_without_audit_reader_grant_cannot_read_audit(
    client: httpx.AsyncClient, seeded_user: User,
) -> None:
    """A fresh admin (Content Manager grant from RBAC-05's admin-grant
    migration, but no Audit Reader grant) still gets 403 -- audit.read is
    carved out of the automatic admin grant, so being an admin is no longer
    sufficient to read the org's audit trail. No regression: the route was
    superadmin-only before this task, so a plain admin already got 403."""
    h = await auth(client, "a@acme.com")
    assert (await client.get("/api/v1/admin/audit", headers=h)).status_code == 403


async def test_superadmin_still_sees_platform_wide(
    client: httpx.AsyncClient, seeded_superadmin: User, seeded_user: User,
    session: AsyncSession,
) -> None:
    """Superadmin's platform-wide view is unaffected -- the org_id query
    param is honored verbatim for superadmin (documented, deferred separation:
    superadmin keeps the platform-wide audit bypass)."""
    await seed_events(session, seeded_user, n=2)
    h = await auth(client, "root@platform.example")
    r = await client.get(
        "/api/v1/admin/audit", headers=h, params={"org_id": str(seeded_user.org_id)}
    )
    assert r.status_code == 200
    assert len(r.json()["events"]) == 2
