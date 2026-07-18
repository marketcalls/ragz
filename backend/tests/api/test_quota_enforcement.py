import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.quotas.models import UsageRecord


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _seed_model_and_workspace_default(
    client: httpx.AsyncClient, chat_env: dict, h_super: dict[str, str], h_admin: dict[str, str]
) -> None:
    r_model = await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h_super,
    )
    r_ws = await client.patch(
        f"/api/v1/workspaces/{chat_env['workspace'].id}",
        json={"default_model_id": r_model.json()["id"]}, headers=h_admin,
    )
    assert r_ws.status_code == 200


async def _send(client: httpx.AsyncClient, h: dict[str, str], ws_id: str) -> httpx.Response:
    r = await client.post("/api/v1/chats", json={"workspace_id": ws_id}, headers=h)
    chat_id = r.json()["id"]
    return await client.post(f"/api/v1/chats/{chat_id}/messages",
                             json={"content": "what was revenue?"}, headers=h)


async def test_chat_records_usage(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession, chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    # Wire the fake streamer/retriever exactly as tests/api/test_chat_stream.py does
    # (FakeStreamer yields usage 42/7); reuse that file's fixture arrangement.
    from tests.conftest import FakeRetriever, FakeStreamer

    app = client._transport.app  # type: ignore[attr-defined]
    app.state.llm_streamer = FakeStreamer()
    app.state.retriever = FakeRetriever(chat_env["document"].id)
    # a registered model must exist; follow test_chat_stream.py's model seeding
    h_super = await auth(client, "root@platform.example")
    h_admin = await auth(client, "a@acme.com")
    await _seed_model_and_workspace_default(client, chat_env, h_super, h_admin)
    r = await _send(client, h_admin, str(chat_env["workspace"].id))
    assert r.status_code == 200
    body = r.text
    assert "done" in body
    rec = (await session.execute(select(UsageRecord))).scalars().all()
    assert len(rec) == 1
    assert (rec[0].prompt_tokens, rec[0].completion_tokens, rec[0].feature) == (42, 7, "chat")
    assert rec[0].user_id == seeded_user.id


async def test_exhausted_quota_blocks_before_streaming(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    seeded_superadmin: User, chat_env: dict, stack_env: None,  # type: ignore[type-arg]
) -> None:
    from tests.conftest import FakeRetriever, FakeStreamer

    app = client._transport.app  # type: ignore[attr-defined]
    app.state.llm_streamer = FakeStreamer()
    app.state.retriever = FakeRetriever(chat_env["document"].id)

    h_super = await auth(client, "root@platform.example")
    r = await client.put(f"/api/v1/admin/orgs/{seeded_user.org_id}/quota", headers=h_super,
                         json={"monthly_tokens": 100, "default_user_monthly_tokens": 10,
                               "reset_day": 1})
    assert r.status_code == 200

    from raghub.modules.quotas.service import record_usage

    await record_usage(session, org_id=seeded_user.org_id, user_id=seeded_user.id,
                       model_id=None, feature="chat", prompt_tokens=10, completion_tokens=0)
    h = await auth(client, "a@acme.com")
    await _seed_model_and_workspace_default(client, chat_env, h_super, h)
    r = await _send(client, h, str(chat_env["workspace"].id))
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Token quota exhausted"
    assert "resets" in r.json()["detail"]


async def test_user_quota_route_admin_scoped(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    h = await auth(client, "a@acme.com")
    assert (
        await client.put(f"/api/v1/users/{plain.id}/quota", json={"monthly_tokens": 5000},
                         headers=h)
    ).status_code == 204
    h_user = await auth(client, "p@acme.com")
    assert (
        await client.put(f"/api/v1/users/{plain.id}/quota", json={"monthly_tokens": 1},
                         headers=h_user)
    ).status_code == 403
