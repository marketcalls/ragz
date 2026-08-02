"""Covers the review finding on Task 11: `_streamer` in api/routes/chats.py must
plumb `request.app.state.litellm_transport` into the real `LiteLLMStreamer` it
constructs (not just into the vkey-generation call), otherwise the master-key
fallback path is untestable under MockTransport and, in production, any
transport override would be silently dropped for the actual completion call.

Unlike every other chat test, these do NOT inject `app.state.llm_streamer` --
that would bypass the exact block under test. They drive the real `_streamer`
path end to end.
"""

import json
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import FakeChunkReader, FakeRetriever


def _sse_body(*, content: str = "Hello world") -> bytes:
    chunks = [
        {"choices": [{"delta": {"content": content}}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    ]
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _gateway_handler(
    *, key_generate_ok: bool, captured_completion_headers: list[dict[str, str]]
) -> httpx.MockTransport:
    """Stands in for the LiteLLM proxy. /v1/model/info, /model/new, /model/delete
    are hit by the admin-model-create + startup sync path; /key/generate and
    /v1/chat/completions are the two calls this fix actually cares about."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/model/info":
            return httpx.Response(200, json={"data": []})
        if path in ("/model/new", "/model/delete"):
            return httpx.Response(200, json={})
        if path == "/key/generate":
            if key_generate_ok:
                return httpx.Response(200, json={"key": "sk-vkey-generated"})
            raise httpx.ConnectError("litellm proxy unreachable")
        if path == "/v1/chat/completions":
            captured_completion_headers.append(dict(request.headers))
            return httpx.Response(
                200, content=_sse_body(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


async def _send_and_collect(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, transport: httpx.MockTransport,
) -> list[tuple[str, dict[str, Any]]]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=transport,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(),
        # llm_streamer deliberately omitted: exercises the real `_streamer` path.
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages", json={"content": "hi"}, headers=h
        )
    assert r.status_code == 200
    return parse_sse(r.text)


async def test_vkey_generation_failure_falls_back_to_master_key_and_still_streams(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    """Before the fix, LiteLLMStreamer was built without the injected transport,
    so this fallback path reached for a real socket and blew up. With the fix,
    the injected MockTransport carries the fallback all the way through and the
    chat still completes successfully -- no 500, no "error" SSE frame."""
    captured: list[dict[str, str]] = []
    transport = _gateway_handler(key_generate_ok=False, captured_completion_headers=captured)
    events = await _send_and_collect(
        engine, redis_client, test_settings, chat_env, session,
        seeded_user, seeded_superadmin, transport,
    )
    names = [e for e, _ in events]
    assert "error" not in names
    assert names[-1] == "done"
    answer = "".join(d["delta"] for e, d in events if e == "token")
    assert answer == "Hello world"

    # The completion call went out over the injected transport with the
    # configured master key, proving the fallback is real (not vacuously true
    # because the request never happened).
    assert len(captured) == 1
    assert captured[0]["authorization"] == f"Bearer {test_settings.litellm_master_key}"


async def test_vkey_generation_success_is_used_as_the_completion_bearer_token(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    captured: list[dict[str, str]] = []
    transport = _gateway_handler(key_generate_ok=True, captured_completion_headers=captured)
    events = await _send_and_collect(
        engine, redis_client, test_settings, chat_env, session,
        seeded_user, seeded_superadmin, transport,
    )
    names = [e for e, _ in events]
    assert "error" not in names
    assert names[-1] == "done"

    assert len(captured) == 1
    assert captured[0]["authorization"] == "Bearer sk-vkey-generated"
