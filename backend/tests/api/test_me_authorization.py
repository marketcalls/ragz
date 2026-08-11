"""RBAC-12: GET /api/v1/me/authorization -- the one endpoint the frontend uses
to render permission-aware nav/actions instead of trusting the fixed JWT role
claim. Backend enforcement (require_action on every route) remains the real
security boundary; this route only reports the caller's own already-computed
TenantContext.
"""
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_me_authorization_reflects_effective_permissions(
    client: httpx.AsyncClient, session: AsyncSession, seeded_user: User,
) -> None:
    # A plain role="user" account, no custom role assigned -- falls back to
    # DEFAULT_USER_PERMISSIONS (search.execute yes, documents.upload no).
    plain = User(
        org_id=seeded_user.org_id, email="plain@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(plain)
    await session.commit()

    member_headers = await auth(client, "plain@acme.com")
    r = await client.get("/api/v1/me/authorization", headers=member_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "user"
    assert "search.execute" in body["permissions"]
    assert "documents.upload" not in body["permissions"]  # DEFAULT_USER_PERMISSIONS, no template
    assert body["policy_version"] is None


async def test_me_authorization_reports_policy_version_when_templated(
    client: httpx.AsyncClient, session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    # Target account the template will be assigned to.
    templated = User(
        org_id=seeded_user.org_id, email="templated@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(templated)
    await session.commit()

    h_super = await auth(client, seeded_superadmin.email)
    h_admin = await auth(client, seeded_user.email)

    # Task 17/18: a template starts "draft" (version 1) and must be activated
    # (status="active") before assign_custom_role will accept it -- activation
    # bumps the version to 2.
    r_create = await client.post(
        "/api/v1/admin/roles", headers=h_super,
        json={"name": "Templated Role", "permissions": ["documents.upload"]},
    )
    assert r_create.status_code == 201
    template_id = r_create.json()["id"]

    r_activate = await client.post(
        f"/api/v1/admin/roles/{template_id}/activate", headers=h_super
    )
    assert r_activate.status_code == 200
    expected_version = r_activate.json()["version"]
    assert expected_version == 2

    r_assign = await client.put(
        f"/api/v1/users/{templated.id}/custom-role", headers=h_admin,
        json={"role_template_id": template_id},
    )
    assert r_assign.status_code == 204

    member_headers_with_template = await auth(client, "templated@acme.com")
    r = await client.get("/api/v1/me/authorization", headers=member_headers_with_template)
    assert r.status_code == 200
    body = r.json()
    assert body["policy_version"] == expected_version
    assert "documents.upload" in body["permissions"]
