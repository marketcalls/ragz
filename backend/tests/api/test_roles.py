import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Organization


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_superadmin_role_template_crud_lifecycle(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")

    r = await client.post("/api/v1/admin/roles", headers=h, json={
        "name": "Engineer", "description": "field engineer",
        "permissions": ["documents.upload", "chat.use"],
    })
    assert r.status_code == 201
    body = r.json()
    template_id = body["id"]
    assert body["name"] == "Engineer"
    assert set(body["permissions"]) == {"documents.upload", "chat.use"}

    listed = (await client.get("/api/v1/admin/roles", headers=h)).json()
    assert any(t["id"] == template_id for t in listed)

    r = await client.patch(
        f"/api/v1/admin/roles/{template_id}", headers=h, json={"description": "updated"}
    )
    assert r.status_code == 200
    assert r.json()["description"] == "updated"

    r = await client.delete(f"/api/v1/admin/roles/{template_id}", headers=h)
    assert r.status_code == 204

    listed = (await client.get("/api/v1/admin/roles", headers=h)).json()
    assert all(t["id"] != template_id for t in listed)


async def test_admin_can_list_but_not_create(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    assert (await client.get("/api/v1/admin/roles", headers=h)).status_code == 200
    r = await client.post(
        "/api/v1/admin/roles", headers=h, json={"name": "ShouldFail", "permissions": []}
    )
    assert r.status_code == 403


async def test_create_with_unknown_permission_flag_is_409(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/roles", headers=h, json={
        "name": "Bad", "permissions": ["not.a.real.flag"],
    })
    assert r.status_code == 409


async def test_delete_while_assigned_is_409(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    super_h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/roles", headers=super_h, json={
        "name": "Engineer2", "permissions": ["documents.upload"],
    })
    template_id = r.json()["id"]
    # RBAC-09: new templates start "draft" and can't be assigned until activated.
    activate = await client.post(f"/api/v1/admin/roles/{template_id}/activate", headers=super_h)
    assert activate.status_code == 200

    plain = User(
        org_id=seeded_user.org_id, email="p@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(plain)
    await session.commit()

    admin_h = await auth(client, "a@acme.com")
    assign = await client.put(
        f"/api/v1/users/{plain.id}/custom-role", headers=admin_h,
        json={"role_template_id": template_id},
    )
    assert assign.status_code == 204

    r = await client.delete(f"/api/v1/admin/roles/{template_id}", headers=super_h)
    assert r.status_code == 409


async def test_admin_assigns_template_and_user_out_reflects_it(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
    seeded_user: User,
    session: AsyncSession,
) -> None:
    super_h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/roles", headers=super_h, json={
        "name": "Engineer3", "permissions": ["documents.upload"],
    })
    template_id = r.json()["id"]
    # RBAC-09: new templates start "draft" and can't be assigned until activated.
    activate = await client.post(f"/api/v1/admin/roles/{template_id}/activate", headers=super_h)
    assert activate.status_code == 200

    plain = User(
        org_id=seeded_user.org_id, email="p3@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(plain)
    await session.commit()

    admin_h = await auth(client, "a@acme.com")
    assign = await client.put(
        f"/api/v1/users/{plain.id}/custom-role", headers=admin_h,
        json={"role_template_id": template_id},
    )
    assert assign.status_code == 204

    users = (await client.get("/api/v1/users", headers=admin_h)).json()
    target = next(u for u in users if u["id"] == str(plain.id))
    assert target["custom_role_id"] == template_id

    # clearing (role_template_id: null) must also round-trip
    clear = await client.put(
        f"/api/v1/users/{plain.id}/custom-role", headers=admin_h,
        json={"role_template_id": None},
    )
    assert clear.status_code == 204
    users = (await client.get("/api/v1/users", headers=admin_h)).json()
    target = next(u for u in users if u["id"] == str(plain.id))
    assert target["custom_role_id"] is None


async def test_assigning_to_admin_succeeds(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    # RBAC-05: an admin-tier target is now a VALID custom-role assignee (an org
    # admin needs an explicit template, e.g. Content Manager, for content-ACL
    # bypass just like a plain user needs one for upload/delete). Superadmin
    # targets are still rejected (covered in test_service.py). Clearing to None
    # here returns 204.
    other_admin = User(
        org_id=seeded_user.org_id, email="a2@acme.com",
        password_hash=seeded_user.password_hash, role="admin",
    )
    session.add(other_admin)
    await session.commit()

    admin_h = await auth(client, "a@acme.com")
    r = await client.put(
        f"/api/v1/users/{other_admin.id}/custom-role", headers=admin_h,
        json={"role_template_id": None},
    )
    assert r.status_code == 204


async def test_cross_org_assignment_is_404(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    rival_org = Organization(name="Rival")
    session.add(rival_org)
    await session.flush()
    rival_user = User(
        org_id=rival_org.id, email="r@rival.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(rival_user)
    await session.commit()

    admin_h = await auth(client, "a@acme.com")
    r = await client.put(
        f"/api/v1/users/{rival_user.id}/custom-role", headers=admin_h,
        json={"role_template_id": None},
    )
    assert r.status_code == 404


async def test_activate_route(
    client: httpx.AsyncClient, seeded_superadmin: User, superadmin_headers: dict[str, str]
) -> None:
    created = (await client.post(
        "/api/v1/admin/roles", headers=superadmin_headers,
        json={"name": "route-activate-test", "permissions": ["chat.read"]},
    )).json()
    r = await client.post(
        f"/api/v1/admin/roles/{created['id']}/activate", headers=superadmin_headers
    )
    assert r.status_code == 200 and r.json()["status"] == "active"


async def test_impact_route(
    client: httpx.AsyncClient, seeded_superadmin: User, superadmin_headers: dict[str, str]
) -> None:
    created = (await client.post(
        "/api/v1/admin/roles", headers=superadmin_headers,
        json={"name": "route-impact-test", "permissions": ["chat.read"]},
    )).json()
    r = await client.get(
        f"/api/v1/admin/roles/{created['id']}/impact", headers=superadmin_headers
    )
    assert r.status_code == 200 and r.json()["affected_users"] == 0
