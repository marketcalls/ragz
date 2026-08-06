"""Task 4: POST /external/v1/chat -- non-streaming collector over the ONE
existing RAG path (stream_reply). Mirrors test_chat_stream.py's fixture setup
(FakeStreamer/FakeRetriever/FakeChunkReader via app.state injection) so
generation is deterministic, but asserts on a single JSON body instead of
parsed SSE frames.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.api_keys_service import generate_api_key
from ragz.modules.auth.models import User
from ragz.modules.chat.models import Chat, Message
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.conftest import FakeChunkReader, FakeRetriever, FakeStreamer, _stub_litellm_handler


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def external_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], fake_streamer: FakeStreamer,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=fake_streamer,
        chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_key(
    client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, test_settings: Settings,
) -> str:
    """Sets a default model on chat_env's workspace (so model resolution
    succeeds), then mints a real API key scoped to that workspace for
    seeded_user (already a WorkspaceMember, via the chat_env fixture)."""
    h_super = await _auth(client, "root@platform.example")
    r_model = await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h_super,
    )
    assert r_model.status_code == 201, r_model.text
    h_admin = await _auth(client, seeded_user.email)
    r_ws = await client.patch(
        f"/api/v1/workspaces/{chat_env['workspace'].id}",
        json={"default_model_id": r_model.json()["id"]}, headers=h_admin,
    )
    assert r_ws.status_code == 200, r_ws.text
    _, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="external-test-key",
        user_id=seeded_user.id, workspace_id=chat_env["workspace"].id, expires_at=None,
    )
    return raw


async def test_external_chat_returns_answer_and_citations(
    external_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(external_client, chat_env, session, seeded_user, test_settings)

    r = await external_client.post(
        "/external/v1/chat", json={"question": "what was revenue?"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Revenue was 12M [1]."
    assert body["no_answer"] is False
    assert body["grounding"] == "documents"
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["marker"] == 1
    assert citation["page"] == 3
    conversation_id = body["conversation_id"]
    UUID(conversation_id)  # well-formed

    # Reusing conversation_id appends to the SAME chat instead of creating a
    # new one -- assert both by message count and by the id staying stable.
    r2 = await external_client.post(
        "/external/v1/chat",
        json={"question": "and the costs?", "conversation_id": conversation_id},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["conversation_id"] == conversation_id

    msgs = (
        await session.execute(
            select(Message).where(Message.chat_id == UUID(conversation_id))
        )
    ).scalars().all()
    # 2 user turns + 2 assistant turns persisted against the one reused chat.
    assert len(msgs) == 4


async def test_external_chat_missing_key_401(external_client: httpx.AsyncClient) -> None:
    r = await external_client.post("/external/v1/chat", json={"question": "hi"})
    assert r.status_code == 401


async def test_external_chat_invalid_key_401(external_client: httpx.AsyncClient) -> None:
    r = await external_client.post(
        "/external/v1/chat", json={"question": "hi"},
        headers={"Authorization": "Bearer ragz_sk_not-a-real-key"},
    )
    assert r.status_code == 401


async def test_external_chat_admin_key_cannot_reach_other_workspace_conversation(
    external_client: httpx.AsyncClient, session: AsyncSession,
    seeded_user: User, test_settings: Settings,
) -> None:
    """Adversarial lock on `_get_or_create_conversation`'s explicit
    `chat.workspace_id != workspace_id` check (external.py). `seeded_user`
    has ORG role "admin" (conftest), which is exactly the role for which
    `tenancy/service.py::get_workspace`'s `ctx.role == "user"` guard is a
    no-op -- so this proves the rejection comes from the route's own check,
    not from get_workspace, for the one role where get_workspace can't be
    trusted to catch it.

    Workspace A and B both belong to seeded_user's org, and seeded_user is a
    member of BOTH (so ownership/membership alone can't explain a 404) --
    the API key is scoped to A only, and the conversation being asked about
    lives in B.
    """
    ws_a = Workspace(org_id=seeded_user.org_id, name="WS-A")
    ws_b = Workspace(org_id=seeded_user.org_id, name="WS-B")
    session.add_all([ws_a, ws_b])
    await session.flush()
    session.add_all([
        WorkspaceMember(workspace_id=ws_a.id, user_id=seeded_user.id),
        WorkspaceMember(workspace_id=ws_b.id, user_id=seeded_user.id),
    ])
    chat_b = Chat(org_id=seeded_user.org_id, workspace_id=ws_b.id, user_id=seeded_user.id)
    session.add(chat_b)
    await session.commit()
    session.add(Message(chat_id=chat_b.id, role="user", content="pre-existing in B"))
    await session.commit()

    count_stmt = select(func.count()).select_from(Message).where(Message.chat_id == chat_b.id)
    before = (await session.execute(count_stmt)).scalar_one()
    assert before == 1

    _, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="cross-ws-key",
        user_id=seeded_user.id, workspace_id=ws_a.id, expires_at=None,
    )

    r = await external_client.post(
        "/external/v1/chat",
        json={"question": "leak?", "conversation_id": str(chat_b.id)},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 404, r.text

    after = (await session.execute(count_stmt)).scalar_one()
    assert after == before  # no user/assistant turn was appended to B's chat
