from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.db import naive_utc
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.chat.models import Chat, Message
from raghub.modules.models.models import Model
from raghub.modules.quotas.service import record_usage
from raghub.modules.tenancy.models import Organization, Workspace


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_usage_me(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    h_super = await auth(client, "root@platform.example")
    await client.put(f"/api/v1/admin/orgs/{seeded_user.org_id}/quota", headers=h_super,
                     json={"monthly_tokens": 100_000, "default_user_monthly_tokens": 1_000,
                           "reset_day": 1})
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=800, completion_tokens=100)
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/usage/me", headers=h)
    body = r.json()
    assert body["used_tokens"] == 900
    assert body["allocated_tokens"] == 1_000
    assert body["warning"] is True
    assert body["resets_at"]


async def test_admin_summary_org_scoped(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=10, completion_tokens=5)
    # foreign-org noise must never appear in this org's summary
    await record_usage(session, org_id=uuid4(), user_id=uuid4(),
                       model_id=None, feature="chat", prompt_tokens=999, completion_tokens=0)
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/admin/usage/summary?days=30", headers=h)
    body = r.json()
    assert sum(d["tokens"] for d in body["by_day"]) == 15
    assert body["by_user"] == [
        {"user_id": str(seeded_user.id), "email": "a@acme.com", "tokens": 15, "queries": 1}
    ]


async def test_platform_usage_requires_superadmin(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=10, completion_tokens=0)
    h_admin = await auth(client, "a@acme.com")
    assert (await client.get("/api/v1/admin/usage/orgs", headers=h_admin)).status_code == 403
    h_super = await auth(client, "root@platform.example")
    rows = (await client.get("/api/v1/admin/usage/orgs", headers=h_super)).json()
    assert {"org_id": str(seeded_user.org_id), "name": "Acme", "tokens": 10} in rows


async def _org_with_workspace(session: AsyncSession, name: str) -> tuple[Organization, Workspace]:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name=f"{name}-WS")
    session.add(ws)
    await session.flush()
    return org, ws


async def test_summary_dashboard_fields_org_scoped(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """kpis/queries_per_day/tokens_by_model_per_day present; totals match ONLY
    this org's seeded rows; foreign-org noise absent (mirrors F's scoping test)."""
    today = naive_utc().date().isoformat()
    model = Model(litellm_model_name="gpt-4o-dash", display_name="gpt-4o",
                 provider_kind="openai")
    session.add(model)
    await session.flush()

    _, ws = await _org_with_workspace(session, "AcmeWS-Home")
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=model.id, feature="chat", prompt_tokens=10, completion_tokens=5)
    chat = Chat(org_id=seeded_user.org_id, workspace_id=ws.id, user_id=seeded_user.id)
    session.add(chat)
    await session.flush()
    session.add(Message(chat_id=chat.id, role="assistant", content="unknown", no_answer=True))
    await session.commit()

    # foreign-org noise: usage + no_answer message must never surface here
    foreign_org, foreign_ws = await _org_with_workspace(session, "Foreign")
    foreign_user = User(org_id=foreign_org.id, email="b@foreign.example",
                        password_hash=hash_password("pw123456"), role="admin")
    session.add(foreign_user)
    await session.flush()
    await record_usage(session, org_id=foreign_org.id, user_id=foreign_user.id,
                       model_id=None, feature="chat", prompt_tokens=999, completion_tokens=0)
    foreign_chat = Chat(org_id=foreign_org.id, workspace_id=foreign_ws.id, user_id=foreign_user.id)
    session.add(foreign_chat)
    await session.flush()
    session.add(Message(chat_id=foreign_chat.id, role="assistant", content="?", no_answer=True))
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/admin/usage/summary?days=30", headers=h)
    body = r.json()
    assert body["kpis"] == {
        "queries": 1, "total_tokens": 15, "active_users": 1, "no_answer_count": 1,
    }
    assert body["queries_per_day"] == [{"day": today, "count": 1}]
    assert body["tokens_by_model_per_day"] == [
        {"day": today, "model_name": "gpt-4o", "tokens": 15}
    ]


async def test_no_answer_count(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """kpis.no_answer_count == seeded no_answer messages in the window."""
    _, ws = await _org_with_workspace(session, "AcmeWS-NoAnswer")
    chat = Chat(org_id=seeded_user.org_id, workspace_id=ws.id, user_id=seeded_user.id)
    session.add(chat)
    await session.flush()
    session.add(Message(chat_id=chat.id, role="assistant", content="a",
                        no_answer=True, sibling_index=0))
    session.add(Message(chat_id=chat.id, role="assistant", content="b",
                        no_answer=True, sibling_index=1))
    # answered message must not be counted
    session.add(Message(chat_id=chat.id, role="assistant", content="c",
                        no_answer=False, sibling_index=2))
    # no_answer message outside the days=30 window must not be counted
    session.add(Message(chat_id=chat.id, role="assistant", content="old",
                        no_answer=True, sibling_index=3,
                        created_at=naive_utc() - timedelta(days=40)))
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/admin/usage/summary?days=30", headers=h)
    assert r.json()["kpis"]["no_answer_count"] == 2


async def test_by_user_carries_query_counts(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """by_user rows gain queries; F's original fields unchanged."""
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=10, completion_tokens=5)
    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=3, completion_tokens=2)
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/admin/usage/summary?days=30", headers=h)
    body = r.json()
    assert body["by_user"] == [
        {"user_id": str(seeded_user.id), "email": "a@acme.com", "tokens": 20, "queries": 2}
    ]
