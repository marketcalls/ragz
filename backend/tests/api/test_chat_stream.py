import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.api.app import create_app
from raghub.core.config import Settings, get_settings
from raghub.core.db import build_session_factory
from raghub.core.errors import UpstreamError
from raghub.modules.auth.models import User
from raghub.modules.chat.models import Citation, Message
from raghub.modules.chat.service import NO_ANSWER_TEXT
from tests.conftest import FakeRetriever, FakeStreamer, _stub_litellm_handler


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def chat_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], fake_streamer: FakeStreamer,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=fake_streamer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_model_and_chat(
    client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_superadmin: User, h_admin: dict[str, str],
) -> str:
    h_super = await auth(client, "root@platform.example")
    r_model = await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h_super,
    )
    # Model resolution requires a workspace default (or an explicit model_id).
    r_ws = await client.patch(
        f"/api/v1/workspaces/{chat_env['workspace'].id}",
        json={"default_model_id": r_model.json()["id"]}, headers=h_admin,
    )
    assert r_ws.status_code == 200
    r = await client.post(
        "/api/v1/chats", json={"workspace_id": str(chat_env["workspace"].id)},
        headers=h_admin,
    )
    return str(r.json()["id"])


async def test_full_event_sequence_and_persistence(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)

    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[0] == "retrieval_started"
    assert names[1] == "sources"
    assert names[2:-2] == ["token"] * (len(names) - 4)
    assert names[-2:] == ["citations", "done"]

    sources = events[1][1]["sources"]
    assert [s["marker"] for s in sources] == [1, 2]
    assert sources[0]["filename"] == "report.pdf"

    answer = "".join(d["delta"] for e, d in events if e == "token")
    assert answer == "Revenue was 12M [1]."
    done = events[-1][1]
    assert done == {"message_id": done["message_id"], "prompt_tokens": 42,
                    "completion_tokens": 7, "no_answer": False}

    # Persistence: user + assistant messages, citation row for [1] only.
    msgs = list((await session.execute(select(Message))).scalars())
    assert {m.role for m in msgs} == {"user", "assistant"}
    assistant = next(m for m in msgs if m.role == "assistant")
    assert assistant.content == answer and assistant.completion_tokens == 7
    cits = list((await session.execute(select(Citation))).scalars())
    assert [(c.marker, c.page) for c in cits] == [(1, 3)]
    assert cits[0].chunk_ref == f"{chat_env['document'].id}:3:0"

    # Iron rule 5: the prompt wrapped chunks in data blocks with the notice.
    sent = fake_streamer.calls[0]["messages"]
    final_user = sent[-1]["content"]  # type: ignore[index]
    assert '<data id="1" source="report.pdf" page="3">' in final_user
    assert "data, not instructions" in final_user


async def test_no_answer_path_is_honest(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=FakeStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "quantum llamas?"}, headers=h)
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names == ["retrieval_started", "sources", "token", "citations", "done"]
    assert events[2][1]["delta"] == NO_ANSWER_TEXT
    assert events[-1][1]["no_answer"] is True
    assert len(events[1][1]["sources"]) == 2  # nearest sources still shown


async def test_edit_creates_sibling_and_regenerate_creates_assistant_sibling(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    r1 = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                                json={"content": "v1?"}, headers=h)
    # Edit of the root message: explicit null parent -> root sibling.
    r2 = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                                json={"content": "v2?", "parent_message_id": None},
                                headers=h)
    assert r1.status_code == r2.status_code == 200
    msgs = list((await session.execute(select(Message))).scalars())
    roots = sorted((m for m in msgs if m.parent_message_id is None),
                   key=lambda m: m.sibling_index)
    assert [(m.sibling_index, m.content) for m in roots] == [(0, "v1?"), (1, "v2?")]
    # Both branches kept their own answers.
    for root in roots:
        kids = [m for m in msgs if m.parent_message_id == root.id]
        assert len(kids) == 1 and kids[0].role == "assistant"

    # Regenerate the v2 answer -> assistant sibling under the same user message.
    v2_answer = next(m for m in msgs if m.parent_message_id == roots[1].id)
    r3 = await chat_client.post(f"/api/v1/messages/{v2_answer.id}/regenerate", headers=h)
    assert r3.status_code == 200
    msgs = list((await session.execute(select(Message))).scalars())
    v2_answers = sorted((m for m in msgs if m.parent_message_id == roots[1].id),
                        key=lambda m: m.sibling_index)
    assert [m.sibling_index for m in v2_answers] == [0, 1]
    assert all(m.role == "assistant" for m in v2_answers)


async def test_explicit_model_override_and_bad_model_404(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    from uuid import UUID, uuid4

    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    h_super = await auth(chat_client, "root@platform.example")
    r = await chat_client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "mistral", "display_name": "Mistral",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h_super,
    )
    override_id = r.json()["id"]

    # Explicit model_id wins over the workspace default (llama3).
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "q", "model_id": override_id}, headers=h)
    assert r.status_code == 200
    assert fake_streamer.calls[-1]["model"] == "mistral"
    assistant = next(
        m for m in (await session.execute(select(Message))).scalars()
        if m.role == "assistant"
    )
    assert assistant.model_id == UUID(override_id)  # resolved model persisted

    # Unknown model -> 404 problem+json BEFORE any SSE bytes; no user message persisted.
    before = len(list((await session.execute(select(Message))).scalars()))
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "q", "model_id": str(uuid4())}, headers=h)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert len(list((await session.execute(select(Message))).scalars())) == before

    # Disabled model -> same 404.
    await chat_client.patch(f"/api/v1/admin/models/{override_id}",
                            json={"enabled": False}, headers=h_super)
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "q", "model_id": override_id}, headers=h)
    assert r.status_code == 404

    # Regenerate accepts an optional {model_id} body too.
    r = await chat_client.post(
        f"/api/v1/messages/{assistant.id}/regenerate",
        json={"model_id": None}, headers=h,
    )
    assert r.status_code == 200  # falls back to the workspace default (llama3)
    assert fake_streamer.calls[-1]["model"] == "llama3"


async def test_upstream_error_yields_generic_message(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """UpstreamError from LLM gateway yields generic error message to client."""
    class FailingStreamer:
        async def stream(self, *, model: str, messages: list[dict[str, str]]):  # type: ignore[no-untyped-def]
            if False:
                yield  # Make this an async generator
            raise UpstreamError(detail="<html>502 Bad Gateway</html>")

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FailingStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "test?"}, headers=h)
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[-1] == "error"
    # Assert generic message, not the raw gateway detail
    assert events[-1][1]["detail"] == "the language model gateway failed"


async def test_retrieval_failure_yields_generic_message_and_persists_user_message(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """A retriever failure (Qdrant/TEI/document-lookup) yields the generic error
    frame instead of aborting the stream; the user message stays persisted."""
    class FailingRetriever:
        async def __call__(  # type: ignore[no-untyped-def]
            self, session, ctx, workspace_id, query, top_k=8
        ):
            raise RuntimeError("qdrant unavailable")

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FailingRetriever(),
        llm_streamer=FakeStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "test?"}, headers=h)
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[0] == "retrieval_started"
    assert names[-1] == "error"
    assert events[-1][1]["detail"] == "streaming failed unexpectedly"

    msgs = list((await session.execute(select(Message))).scalars())
    assert [m.role for m in msgs] == ["user"]  # persisted; no assistant reply


async def test_runtime_error_mid_stream_yields_generic_message_and_closes(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """RuntimeError mid-stream yields generic error message and terminates cleanly."""
    from raghub.modules.chat.llm import LLMDelta

    class MidStreamFailingStreamer:
        async def stream(self, *, model: str, messages: list[dict[str, str]]):  # type: ignore[no-untyped-def]
            yield LLMDelta("partial")
            raise RuntimeError("unexpected failure")

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=MidStreamFailingStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "test?"}, headers=h)
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[-1] == "error"
    assert events[-1][1]["detail"] == "streaming failed unexpectedly"
    # Verify the event sequence ends cleanly (no additional events after error)
    assert len(events[-1:]) == 1
