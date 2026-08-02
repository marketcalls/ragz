import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import WorkspaceMember


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_admin_field_crud_lifecycle(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    ws = chat_env["workspace"]
    h = await auth(client, "a@acme.com")

    r = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h)
    assert r.status_code == 200
    assert {f["name"] for f in r.json()} == {"department", "doc_type", "revision_date"}

    r = await client.post(
        f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h,
        json={"name": "project_code", "label": "Project Code", "field_type": "text"},
    )
    assert r.status_code == 201
    field_id = r.json()["id"]

    r = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h)
    assert r.status_code == 200
    assert {f["name"] for f in r.json()} == {
        "department", "doc_type", "revision_date", "project_code",
    }

    r = await client.delete(f"/api/v1/metadata-fields/{field_id}", headers=h)
    assert r.status_code == 204

    r = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h)
    assert r.status_code == 200
    assert {f["name"] for f in r.json()} == {"department", "doc_type", "revision_date"}


async def test_member_can_list_fields_but_not_create(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    ws = chat_env["workspace"]
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=plain.id))
    await session.commit()

    h = await auth(client, "p@acme.com")

    # GET is member-gated: the filter bar and per-doc Tags dialog need the
    # field list for any workspace member, not just admins.
    r = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h)
    assert r.status_code == 200
    assert {f["name"] for f in r.json()} == {"department", "doc_type", "revision_date"}

    # POST/DELETE stay admin-only.
    r = await client.post(
        f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h,
        json={"name": "x", "label": "X", "field_type": "text"},
    )
    assert r.status_code == 403


async def test_member_sets_document_metadata_values(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    ws = chat_env["workspace"]
    doc = chat_env["document"]
    plain = User(org_id=seeded_user.org_id, email="p2@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=plain.id))
    await session.commit()

    h_admin = await auth(client, "a@acme.com")
    # seed the preset fields so "department"/"doc_type" are known keys
    seed = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h_admin)
    assert seed.status_code == 200

    h_member = await auth(client, "p2@acme.com")
    r = await client.put(
        f"/api/v1/documents/{doc.id}/metadata", headers=h_member,
        json={"values": {"department": "engineering", "doc_type": "manual"}},
    )
    assert r.status_code == 200
    assert r.json()["meta"] == {"department": "engineering", "doc_type": "manual"}
