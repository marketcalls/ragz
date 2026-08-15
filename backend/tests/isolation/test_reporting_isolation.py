"""Adversarial isolation for the scoped cost-reporting API (iron rule 1,
design 2026-08-15 §2 Phase 2b).

Proves, over the real HTTP stack, that:
  * self scope never returns another user's rows;
  * org scope never crosses org_id;
  * department scope only returns the caller's OWNED/MANAGED workspaces;
  * platform scope 403s a non-superadmin and returns cross-org data only for
    a superadmin;
  * a plain member (reports.view.self floor only) is 403'd on every wider scope.
"""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.quotas.models import UsageRecord
from ragz.modules.tenancy.models import Organization, Workspace, WorkspaceMember


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def env(session: AsyncSession, seeded_user: User) -> dict[str, Any]:
    """seeded_user is the Acme admin (a@acme.com). Adds an Acme plain member,
    two Acme workspaces (admin OWNS ws1 only), and a rival org with its own
    usage -- the cross-tenant bait."""
    org_id = seeded_user.org_id
    member = User(org_id=org_id, email="m@acme.com",
                  password_hash=hash_password("pw123456"), role="user")
    ws1 = Workspace(org_id=org_id, name="acme-ws1")
    ws2 = Workspace(org_id=org_id, name="acme-ws2")
    session.add_all([member, ws1, ws2])
    await session.flush()
    # Admin manages ONLY ws1 (its department). No membership in ws2.
    session.add(WorkspaceMember(workspace_id=ws1.id, user_id=seeded_user.id, role="owner"))

    rival = Organization(name="RivalCo")
    session.add(rival)
    await session.flush()
    rival_user = User(org_id=rival.id, email="r@rival.com",
                      password_hash=hash_password("pw123456"), role="user")
    session.add(rival_user)
    await session.flush()

    session.add_all([
        UsageRecord(org_id=org_id, user_id=seeded_user.id, workspace_id=ws1.id,
                    model_id=None, feature="chat", prompt_tokens=100, completion_tokens=50),
        UsageRecord(org_id=org_id, user_id=seeded_user.id, workspace_id=ws2.id,
                    model_id=None, feature="chat", prompt_tokens=200, completion_tokens=100),
        UsageRecord(org_id=org_id, user_id=member.id, workspace_id=ws1.id,
                    model_id=None, feature="chat", prompt_tokens=10, completion_tokens=5),
        UsageRecord(org_id=rival.id, user_id=rival_user.id, model_id=None,
                    feature="chat", prompt_tokens=9999, completion_tokens=0),
    ])
    await session.commit()
    return {"admin": seeded_user, "member": member, "ws1": ws1, "ws2": ws2,
            "rival_user": rival_user}


async def test_self_scope_never_leaks_another_user(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    h = await auth(client, "m@acme.com")
    r = await client.get("/api/v1/reports/usage",
                         params={"scope": "self", "group_by": "user"}, headers=h)
    assert r.status_code == 200
    groups = {row["group"] for row in r.json()["rows"]}
    assert groups == {str(env["member"].id)}  # only the caller
    assert str(env["admin"].id) not in groups


async def test_org_scope_never_crosses_org(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    h = await auth(client, "a@acme.com")  # Acme admin auto-holds reports.view.org
    r = await client.get("/api/v1/reports/usage",
                         params={"scope": "org", "group_by": "user"}, headers=h)
    assert r.status_code == 200
    rows = r.json()["rows"]
    groups = {row["group"] for row in rows}
    assert str(env["admin"].id) in groups and str(env["member"].id) in groups
    assert str(env["rival_user"].id) not in groups  # rival org excluded
    assert all(row["prompt_tokens"] != 9999 for row in rows)


async def test_department_scope_only_managed_workspaces(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    h = await auth(client, "a@acme.com")  # owns ws1 only
    r = await client.get("/api/v1/reports/usage",
                         params={"scope": "department", "group_by": "workspace"}, headers=h)
    assert r.status_code == 200
    groups = {row["group"] for row in r.json()["rows"]}
    assert groups == {str(env["ws1"].id)}  # ws2 (not managed) excluded
    assert str(env["ws2"].id) not in groups


async def test_platform_scope_forbidden_for_non_superadmin(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    # Even an org admin (who auto-holds the reports.view.platform ACTION) is
    # denied -- the handler gates platform on ctx.role == "superadmin", not the
    # auto-granted action.
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/reports/usage",
                         params={"scope": "platform", "group_by": "user"}, headers=h)
    assert r.status_code == 403


async def test_platform_scope_returns_cross_org_for_superadmin(
    client: httpx.AsyncClient, env: dict[str, Any], seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/reports/usage",
                         params={"scope": "platform", "group_by": "user"}, headers=h)
    assert r.status_code == 200
    groups = {row["group"] for row in r.json()["rows"]}
    # Sees BOTH Acme users AND the rival org's user -- cross-org, by design.
    assert str(env["admin"].id) in groups
    assert str(env["rival_user"].id) in groups


async def test_member_denied_all_wider_scopes(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    """A plain member holds only reports.view.self; department/org/platform
    are all 403, while self is 200 (the floor works)."""
    h = await auth(client, "m@acme.com")
    assert (await client.get("/api/v1/reports/usage",
            params={"scope": "self"}, headers=h)).status_code == 200
    for scope in ("department", "org", "platform"):
        r = await client.get("/api/v1/reports/usage",
                             params={"scope": scope}, headers=h)
        assert r.status_code == 403, scope


async def test_csv_export_injection_guard_and_scope(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    """Export mirrors the on-screen scope AND neutralizes CSV-injection: a
    group cell whose value could start with a formula lead-in is prefixed with
    a quote. group_by=feature yields plain feature names here, so we assert the
    header + attachment wiring and org-scope content; the guard itself is unit-
    tested below for lead-in chars."""
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/reports/usage/export",
                         params={"scope": "org", "group_by": "feature"}, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "usage-report.csv" in r.headers["content-disposition"]
    lines = r.text.strip().split("\n")
    assert lines[0] == "group,prompt_tokens,completion_tokens,units,cost_usd"
    # rival's 9999 row must not appear in an org-scoped Acme export
    assert "9999" not in r.text


def test_csv_cell_guard_neutralizes_formula_lead_ins() -> None:
    from ragz.api.routes.reports import _csv_cell

    assert _csv_cell("=SUM(A1:A9)") == "'=SUM(A1:A9)"
    assert _csv_cell("+1") == "'+1"
    assert _csv_cell("-1") == "'-1"
    assert _csv_cell("@x") == "'@x"
    assert _csv_cell("chat") == "chat"  # ordinary value untouched
    # embedded comma/quote still gets RFC-4180 quoted
    assert _csv_cell('a,b"c') == '"a,b""c"'
