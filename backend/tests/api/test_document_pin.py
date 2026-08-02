from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.documents.models import Document
from ragz.modules.documents.service import list_pinned_documents
from ragz.modules.tenancy.context import TenantContext


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_pin_and_unpin_roundtrip(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict[str, Any]
) -> None:
    doc = chat_env["document"]
    h = await auth(client, "a@acme.com")
    r = await client.patch(f"/api/v1/documents/{doc.id}", json={"pinned": True}, headers=h)
    assert r.status_code == 200 and r.json()["pinned"] is True
    listed = (await client.get(
        f"/api/v1/workspaces/{chat_env['workspace'].id}/documents", headers=h)).json()
    assert [d["pinned"] for d in listed] == [True]
    r2 = await client.patch(f"/api/v1/documents/{doc.id}", json={"pinned": False}, headers=h)
    assert r2.status_code == 200 and r2.json()["pinned"] is False


async def test_pin_cross_org_is_404(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict[str, Any],
    session: AsyncSession,
) -> None:
    from ragz.modules.auth.passwords import hash_password
    from ragz.modules.tenancy.models import Organization

    other = Organization(name="OtherOrg")
    session.add(other)
    await session.flush()
    session.add(User(org_id=other.id, email="o@other.com",
                     password_hash=hash_password("pw123456"), role="admin"))
    await session.commit()
    h = await auth(client, "o@other.com")
    r = await client.patch(f"/api/v1/documents/{chat_env['document'].id}",
                           json={"pinned": True}, headers=h)
    assert r.status_code == 404


async def test_list_pinned_only_returns_indexed(
    session: AsyncSession, seeded_user: User, chat_env: dict[str, Any]
) -> None:
    ws = chat_env["workspace"]
    indexed: Document = chat_env["document"]
    indexed.pinned = True
    processing = Document(org_id=seeded_user.org_id, workspace_id=ws.id,
                          filename="wip.pdf", mime="application/pdf", size_bytes=1,
                          content_hash="h2", status="processing", storage_key="k2",
                          created_by=seeded_user.id, pinned=True, lineage_id=uuid4())
    session.add(processing)
    await session.commit()
    ctx = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                        role="admin", workspace_ids=frozenset({ws.id}))
    pinned = await list_pinned_documents(session, ctx, ws.id)
    assert [d.id for d in pinned] == [indexed.id]
