"""RBAC-2/RBAC-06: granular permission resolution + the require_action guard.

Probe-route pattern borrowed from tests/api/test_tenant_context.py: wire a
throwaway route onto the already-built app so we can exercise
get_tenant_context/require_action through a real request instead of
constructing TenantContext by hand.
"""

import httpx
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.context import TenantContext, get_tenant_context, require_action
from ragz.modules.tenancy.models import RoleTemplate
from ragz.modules.tenancy.permissions import DEFAULT_USER_PERMISSIONS, PERMISSIONS


def wire_probe(app: FastAPI) -> None:
    @app.get("/probe/permissions")
    async def permissions(ctx: TenantContext = Depends(get_tenant_context)) -> dict[str, list[str]]:  # noqa: B008
        return {"permissions": sorted(ctx.permissions)}

    @app.get(
        "/probe/requires-delete",
        dependencies=[Depends(require_action("documents.delete"))],
    )
    async def requires_delete() -> dict[str, bool]:
        return {"ok": True}


async def login_token(client: httpx.AsyncClient, email: str, pw: str = "pw123456") -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return str(r.json()["access_token"])


async def test_admin_ctx_has_all_permissions(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    tok = await login_token(client, "a@acme.com")
    r = await client.get("/probe/permissions", headers={"Authorization": f"Bearer {tok}"})
    assert set(r.json()["permissions"]) == set(PERMISSIONS)


async def test_plain_user_has_exactly_default_permissions(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    plain = User(
        org_id=seeded_user.org_id, email="p@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(plain)
    await session.commit()
    tok = await login_token(client, "p@acme.com")
    r = await client.get("/probe/permissions", headers={"Authorization": f"Bearer {tok}"})
    assert set(r.json()["permissions"]) == set(DEFAULT_USER_PERMISSIONS)


async def test_user_with_template_has_exactly_template_permissions(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    template = RoleTemplate(name="Engineer", permissions=["documents.upload", "chat.use"])
    session.add(template)
    await session.flush()
    engineer = User(
        org_id=seeded_user.org_id, email="e@acme.com",
        password_hash=seeded_user.password_hash, role="user", custom_role_id=template.id,
    )
    session.add(engineer)
    await session.commit()
    tok = await login_token(client, "e@acme.com")
    r = await client.get("/probe/permissions", headers={"Authorization": f"Bearer {tok}"})
    assert set(r.json()["permissions"]) == {"documents.upload", "chat.use"}


async def test_user_with_dangling_custom_role_id_falls_back_to_default(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """Missing template row (e.g. deleted out from under a stale FK) must not
    500 or silently grant zero permissions -- it falls back to
    DEFAULT_USER_PERMISSIONS exactly like no-template-assigned."""
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    template = RoleTemplate(name="Temp", permissions=["chat.use"])
    session.add(template)
    await session.flush()
    dangling_id = template.id
    orphan = User(
        org_id=seeded_user.org_id, email="orphan@acme.com",
        password_hash=seeded_user.password_hash, role="user", custom_role_id=dangling_id,
    )
    session.add(orphan)
    await session.commit()
    await session.delete(template)
    await session.commit()
    tok = await login_token(client, "orphan@acme.com")
    r = await client.get("/probe/permissions", headers={"Authorization": f"Bearer {tok}"})
    assert set(r.json()["permissions"]) == set(DEFAULT_USER_PERMISSIONS)


async def test_require_permission_guard(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User, session: AsyncSession
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    template = RoleTemplate(name="Engineer2", permissions=["documents.upload", "chat.use"])
    session.add(template)
    await session.flush()
    engineer = User(
        org_id=seeded_user.org_id, email="e2@acme.com",
        password_hash=seeded_user.password_hash, role="user", custom_role_id=template.id,
    )
    plain = User(
        org_id=seeded_user.org_id, email="p2@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add_all([engineer, plain])
    await session.commit()

    engineer_tok = await login_token(client, "e2@acme.com")
    plain_tok = await login_token(client, "p2@acme.com")
    admin_tok = await login_token(client, "a@acme.com")
    super_tok = await login_token(client, "root@platform.example")

    def h(tok: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tok}"}

    assert (await client.get("/probe/requires-delete", headers=h(engineer_tok))).status_code == 403
    # RBAC-04 deny-by-default: a plain user with no custom role no longer holds
    # documents.delete (it left DEFAULT_USER_PERMISSIONS), so the guard denies.
    assert (await client.get("/probe/requires-delete", headers=h(plain_tok))).status_code == 403
    assert (await client.get("/probe/requires-delete", headers=h(admin_tok))).status_code == 200
    assert (await client.get("/probe/requires-delete", headers=h(super_tok))).status_code == 200


def test_catalog_is_a_superset_of_every_legacy_flag() -> None:
    legacy = {"documents.upload", "documents.delete", "workspace.configure",
              "analytics.view", "chat.use"}
    assert legacy <= PERMISSIONS


def test_new_action_catalog_covers_every_declared_domain() -> None:
    for action in (
        "search.execute", "chat.read", "chat.generate", "documents.content.read",
        "documents.acl.bypass", "audit.read", "audit.export", "roles.assign",
        "users.role.assign", "groups.manage", "quota.manage", "evals.run",
    ):
        assert action in PERMISSIONS


def test_default_user_permissions_is_non_destructive() -> None:
    """RBAC-04 deny-by-default: the fallback for a "user"-tier account with no
    custom role is the NON-DESTRUCTIVE read/search/chat floor. The legacy
    destructive/legacy flags (documents.upload, documents.delete, and the
    retired chat.use) are NO LONGER in the default -- an account needs an
    explicit role (e.g. the seeded "Contributor" that Task 4's migration
    assigns to every pre-existing user) to upload/delete. The read floor is
    still present so ordinary members can list/search/read/chat by default."""
    destructive_or_legacy = {"documents.upload", "documents.delete", "chat.use"}
    assert destructive_or_legacy.isdisjoint(DEFAULT_USER_PERMISSIONS)
    assert {
        "workspace.read", "documents.list", "documents.content.read",
        "search.execute", "chat.read", "chat.generate",
    } <= DEFAULT_USER_PERMISSIONS
