from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Group, UserGroup, WorkspaceMember
from tests.api.test_permissions_routes import make_templated_member


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Docs"}, headers=h)
    return str(r.json()["id"])


async def upload(
    client: httpx.AsyncClient, h: dict[str, str], ws_id: str,
    filename: str = "notes.txt", content: bytes = b"the flux capacitor hums",
) -> dict:  # type: ignore[type-arg]
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": (filename, content, "text/plain")},
    )
    assert r.status_code == 201
    return r.json()  # type: ignore[no-any-return]


async def test_member_can_fetch_file_bytes(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    doc = await upload(client, h, ws_id, "notes.txt", b"the flux capacitor hums")

    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)
    assert r.status_code == 200
    assert r.content == b"the flux capacitor hums"
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"] == 'inline; filename="notes.txt"'


async def test_non_group_member_denied_restricted_document_bytes(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
) -> None:
    """The key ACL test: a plain member who is a workspace member (and can
    therefore SEE the restricted document in listings) but is NOT in its ACL
    group must be denied the file bytes -- non-leaking (same error class the
    service layer already uses for restricted-document access), and no bytes
    returned."""
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    doc = await upload(client, h_admin, ws_id, "secret.txt", b"the acquisition price is 4400")

    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.commit()
    r_acl = await client.put(
        f"/api/v1/documents/{doc['id']}/acl", headers=h_admin,
        json={"acl_group_ids": [str(group.id)]},
    )
    assert r_acl.status_code == 200

    outsider = User(org_id=seeded_user.org_id, email="out@acme.com",
                     password_hash=seeded_user.password_hash, role="user")
    session.add(outsider)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=outsider.id))
    await session.commit()

    h_out = await auth(client, "out@acme.com")

    # Existence is still visible in the listing (Drive-style)...
    listing = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h_out)
    assert doc["id"] in [d["id"] for d in listing.json()]

    # ...but the content endpoint must deny it and return no bytes.
    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h_out)
    assert r.status_code in (403, 404)
    assert r.content != b"the acquisition price is 4400"
    assert b"4400" not in r.content


async def test_group_member_can_fetch_restricted_document_bytes(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
) -> None:
    """Positive control for the ACL test above: a member who IS in the
    document's ACL group gets the bytes."""
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    doc = await upload(client, h_admin, ws_id, "secret.txt", b"the acquisition price is 4400")

    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.commit()
    r_acl = await client.put(
        f"/api/v1/documents/{doc['id']}/acl", headers=h_admin,
        json={"acl_group_ids": [str(group.id)]},
    )
    assert r_acl.status_code == 200

    member = User(org_id=seeded_user.org_id, email="in@acme.com",
                  password_hash=seeded_user.password_hash, role="user")
    session.add(member)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=member.id))
    session.add(UserGroup(group_id=group.id, user_id=member.id))
    await session.commit()

    h_in = await auth(client, "in@acme.com")
    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h_in)
    assert r.status_code == 200
    assert r.content == b"the acquisition price is 4400"


async def test_role_denied_content_read_action_gets_403(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
) -> None:
    """Route-policy/enforcement gate: a custom role WITHOUT
    documents.content.read (but WITH workspace membership) must be denied
    even though it could reach get_document_checked/user_can_access_document
    successfully -- require_action is the outer gate."""
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    doc = await upload(client, h_admin, ws_id)

    await make_templated_member(
        session, seeded_user, email="norights@acme.com", template_name="NoContentRead",
        permissions=["documents.list"], workspace_id=ws_id,
    )
    h = await auth(client, "norights@acme.com")
    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)
    assert r.status_code == 403


async def test_unknown_document_id_returns_404(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.get(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000/file", headers=h
    )
    assert r.status_code == 404
