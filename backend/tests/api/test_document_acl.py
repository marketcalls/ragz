import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.documents.service import get_document_checked
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Group, WorkspaceMember


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_set_and_clear_acl(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    doc = chat_env["document"]
    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.put(f"/api/v1/documents/{doc.id}/acl",
                         json={"acl_group_ids": [str(group.id)]}, headers=h)
    assert r.status_code == 200
    assert r.json()["acl_group_ids"] == [str(group.id)]

    r = await client.put(f"/api/v1/documents/{doc.id}/acl",
                         json={"acl_group_ids": None}, headers=h)
    assert r.status_code == 200
    assert r.json()["acl_group_ids"] is None


async def test_acl_rejects_empty_list(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    """[] is invalid: clearing a restriction is `null`, never `[]` — a wire-
    level `[]` would otherwise be ambiguous with "restricted to no groups"
    (get_document_checked's `is not None` gate), silently colliding with the
    "unrestricted" meaning the payload shape suggests. Rejected at the schema
    layer (422) before it ever reaches set_document_acl."""
    doc = chat_env["document"]
    h = await auth(client, "a@acme.com")
    r = await client.put(f"/api/v1/documents/{doc.id}/acl",
                         json={"acl_group_ids": []}, headers=h)
    assert r.status_code == 422


async def test_acl_requires_field(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    """acl_group_ids is required-but-nullable: the body must explicitly carry
    the key (null to clear, a non-empty list to set); omitting it entirely is
    rejected with 422 rather than silently defaulting to "clear"."""
    doc = chat_env["document"]
    h = await auth(client, "a@acme.com")
    r = await client.put(f"/api/v1/documents/{doc.id}/acl", json={}, headers=h)
    assert r.status_code == 422


async def test_acl_rejects_foreign_group(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    from ragz.modules.tenancy.models import Organization

    other = Organization(name="Foreign")
    session.add(other)
    await session.flush()
    foreign = Group(org_id=other.id, name="finance")
    session.add(foreign)
    await session.commit()
    h = await auth(client, "a@acme.com")
    r = await client.put(f"/api/v1/documents/{chat_env['document'].id}/acl",
                         json={"acl_group_ids": [str(foreign.id)]}, headers=h)
    assert r.status_code == 404


async def test_restricted_document_direct_access_denied(
    session: AsyncSession, seeded_user: User, chat_env: dict  # type: ignore[type-arg]
) -> None:
    import pytest

    from ragz.core.errors import WorkspaceAccessDenied

    doc = chat_env["document"]
    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.flush()
    doc.acl_group_ids = [group.id]
    await session.commit()

    outsider = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id, role="user",
                             workspace_ids=frozenset({doc.workspace_id}))
    with pytest.raises(WorkspaceAccessDenied):
        await get_document_checked(session, outsider, doc.id)

    member = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id, role="user",
                           workspace_ids=frozenset({doc.workspace_id}),
                           group_ids=frozenset({group.id}))
    assert (await get_document_checked(session, member, doc.id)).id == doc.id
    admin = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id, role="admin",
                          workspace_ids=frozenset())
    assert (await get_document_checked(session, admin, doc.id)).id == doc.id


async def test_listing_blanks_acl_group_ids_for_plain_user_but_shows_admin(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    """The restricted document still shows up in the listing for a plain
    workspace member (existence stays visible, Drive-style) but
    `acl_group_ids` -- admin-only metadata -- must be null for them, while
    an admin sees the real group ids."""
    ws = chat_env["workspace"]
    doc = chat_env["document"]
    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.flush()
    doc.acl_group_ids = [group.id]

    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=plain.id))
    await session.commit()

    h_admin = await auth(client, "a@acme.com")
    r_admin = await client.get(f"/api/v1/workspaces/{ws.id}/documents", headers=h_admin)
    assert r_admin.status_code == 200
    admin_row = next(d for d in r_admin.json() if d["id"] == str(doc.id))
    assert admin_row["acl_group_ids"] == [str(group.id)]

    h_user = await auth(client, "p@acme.com")
    r_user = await client.get(f"/api/v1/workspaces/{ws.id}/documents", headers=h_user)
    assert r_user.status_code == 200
    user_row = next(d for d in r_user.json() if d["id"] == str(doc.id))
    assert user_row["acl_group_ids"] is None
