"""Step B: OpenAI-compatible `/external/v1/openai/chat/completions` +
`/external/v1/openai/models`. Mirrors test_external_chat.py's fixture setup
(FakeStreamer/FakeRetriever/FakeChunkReader via app.state injection, API key
minted via generate_api_key) so generation is deterministic, but asserts on
the OpenAI chat.completion / model-list shapes instead of Ragz's native ones.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.api_keys_service import generate_api_key
from ragz.modules.auth.models import User
from tests.conftest import FakeChunkReader, FakeRetriever, FakeStreamer, _stub_litellm_handler


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def openai_client(
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
        session, test_settings, actor_id=seeded_user.id, name="openai-compat-test-key",
        user_id=seeded_user.id, workspace_id=chat_env["workspace"].id, expires_at=None,
    )
    return raw


async def test_openai_chat_completions_returns_openai_shape_with_x_ragz(
    openai_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(openai_client, chat_env, session, seeded_user, test_settings)

    r = await openai_client.post(
        "/external/v1/openai/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "what was revenue?"},
            ],
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    assert body["model"] == "gpt-4o-mini"
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Revenue was 12M [1]."
    assert body["usage"] == {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    }
    x_ragz = body["x_ragz"]
    assert x_ragz["no_answer"] is False
    assert x_ragz["grounding"] == "documents"
    assert len(x_ragz["citations"]) == 1
    assert x_ragz["citations"][0]["marker"] == 1
    UUID(x_ragz["conversation_id"])  # well-formed


async def test_openai_chat_completions_defaults_model_when_absent(
    openai_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(openai_client, chat_env, session, seeded_user, test_settings)

    r = await openai_client.post(
        "/external/v1/openai/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "ragz"


async def test_openai_models_list(
    openai_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(openai_client, chat_env, session, seeded_user, test_settings)

    r = await openai_client.get(
        "/external/v1/openai/models", headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    model = body["data"][0]
    assert model["id"] == "llama3"
    assert model["object"] == "model"
    assert model["owned_by"] == "ragz"


async def test_openai_models_list_missing_key_401(openai_client: httpx.AsyncClient) -> None:
    r = await openai_client.get("/external/v1/openai/models")
    assert r.status_code == 401


async def test_openai_chat_completions_missing_key_401(
    openai_client: httpx.AsyncClient,
) -> None:
    r = await openai_client.post(
        "/external/v1/openai/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


async def test_openai_chat_completions_stream_true_rejected_400(
    openai_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(openai_client, chat_env, session, seeded_user, test_settings)

    r = await openai_client.post(
        "/external/v1/openai/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 400, r.text


async def test_openai_chat_completions_no_user_message_rejected(
    openai_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(openai_client, chat_env, session, seeded_user, test_settings)

    r = await openai_client.post(
        "/external/v1/openai/chat/completions",
        json={"messages": [{"role": "system", "content": "be nice"}]},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code in (400, 422), r.text


async def test_openai_chat_completions_empty_messages_rejected(
    openai_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, test_settings: Settings,
) -> None:
    raw = await _make_key(openai_client, chat_env, session, seeded_user, test_settings)

    r = await openai_client.post(
        "/external/v1/openai/chat/completions",
        json={"messages": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code in (400, 422), r.text
