import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth.models import User
from ragz.modules.documents.models import Document
from ragz.modules.tenancy.models import WorkspaceMember


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_approve_requires_admin_role(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    """A plain workspace member (role="user") must be rejected: approval is
    an AdminDep-gated route, not a per-permission check inline in the handler
    (iron rule 4)."""
    ws = chat_env["workspace"]
    doc = chat_env["document"]
    member = User(org_id=seeded_user.org_id, email="member@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(member)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id))
    await session.commit()

    h = await auth(client, "member@acme.com")
    r = await client.put(f"/api/v1/documents/{doc.id}/approved",
                         json={"approved": True}, headers=h)
    assert r.status_code == 403


async def test_approve_cross_org_is_404(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, session: AsyncSession,
    stack_env: None,  # type: ignore[type-arg]
) -> None:
    """An admin from a DIFFERENT org must get the same 404 as "not found" --
    existence of another org's document must never leak (RBAC-5 posture)."""
    from ragz.modules.auth.passwords import hash_password
    from ragz.modules.tenancy.models import Organization

    other = Organization(name="OtherOrg")
    session.add(other)
    await session.flush()
    session.add(User(org_id=other.id, email="o@other.com",
                     password_hash=hash_password("pw123456"), role="admin"))
    await session.commit()

    h = await auth(client, "o@other.com")
    r = await client.put(f"/api/v1/documents/{chat_env['document'].id}/approved",
                         json={"approved": True}, headers=h)
    assert r.status_code == 404


async def test_approve_happy_path_sets_current_and_audits(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, session: AsyncSession,
    stack_env: None,  # type: ignore[type-arg]
) -> None:
    """Admin, same org, happy path: 200 with approved/is_current flipped, and
    an audit row for "document.approved" is recorded."""
    doc: Document = chat_env["document"]
    doc.vectors_present = True  # simulate a document whose points are already indexed
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.put(f"/api/v1/documents/{doc.id}/approved",
                         json={"approved": True}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["approved"] is True
    assert body["is_current"] is True

    events = (
        await session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "document.approved",
                AuditEvent.target_id == str(doc.id),
            )
        )
    ).scalars().all()
    assert len(events) == 1
