import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.documents.service import get_document_checked
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Group


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


async def test_acl_rejects_foreign_group(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    from raghub.modules.tenancy.models import Organization

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

    from raghub.core.errors import WorkspaceAccessDenied

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
