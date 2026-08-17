import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.core.errors import UpstreamError
from ragz.modules.auth.models import User
from ragz.modules.chat.llm import LLMCompletion, LLMUsage
from ragz.modules.chat.models import Chat, Citation, Message
from ragz.modules.chat.service import NO_ANSWER_TEXT
from ragz.modules.models.models import Model
from ragz.modules.quotas.models import UsageRecord
from ragz.modules.retrieval.service import RetrievedChunk
from tests.conftest import (
    FakeChunkReader,
    FakeCompleter,
    FakeRetriever,
    FakeStreamer,
    _stub_litellm_handler,
)


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
        chunk_reader=FakeChunkReader(),
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


async def _disable_generative_ui(client: httpx.AsyncClient, h_super: dict[str, str]) -> None:
    """Turn OFF the global generative-UI setting (default ON). Used by tests
    that inject a completer for a NON-generative-UI purpose and must not have
    the visualize step run (extra completer call / blocks frame / token totals)."""
    r = await client.put(
        "/api/v1/admin/settings", json={"generative_ui_enabled": False}, headers=h_super,
    )
    assert r.status_code == 200


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
                    "completion_tokens": 7, "no_answer": False,
                    "grounding": "documents", "validation_failed": False}

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


async def test_sources_and_citations_carry_section_and_version(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """CHAT-4: the `sources`/`citations` SSE frames and the persisted Citation
    row carry the chunk's section path and the document's OWN version (fetched
    from the same get_document_checked call as the filename - not a chunk-local
    guess). chunk_ref identity (H-C3) is untouched by this."""
    document = chat_env["document"]
    document.version = 2
    await session.commit()

    retriever = FakeRetriever(document.id)
    retriever.chunks = [
        RetrievedChunk(document_id=document.id, page=3, chunk_index=0,
                       text="Evacuate via the north stairwell.", score=0.91,
                       section="Fire Safety > Evacuation", version=2),
    ]
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=retriever,
        llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "evacuation?"}, headers=h)
    assert r.status_code == 200
    frames = [{"event": e, "data": d} for e, d in parse_sse(r.text)]

    sources_frame = next(f for f in frames if f["event"] == "sources")
    src = sources_frame["data"]["sources"][0]
    assert src["section"] == "Fire Safety > Evacuation"
    assert src["version"] == 2

    citations_frame = next(f for f in frames if f["event"] == "citations")
    cit = citations_frame["data"]["citations"][0]
    assert cit["section"] == "Fire Safety > Evacuation" and cit["version"] == 2

    cits = list((await session.execute(select(Citation))).scalars())
    assert cits[0].section == "Fire Safety > Evacuation" and cits[0].version == 2


async def test_no_answer_path_is_honest(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """decline-policy workspace: the pre-Plan-I no-answer contract, byte-
    identical (Plan I's default policy is general_knowledge, so this test
    opts the workspace into decline explicitly - see
    test_weak_retrieval_general_knowledge_fallback for the new default path)."""
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r_patch = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"fallback_policy": "decline"}, headers=h,
        )
        assert r_patch.status_code == 200
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "quantum llamas?"}, headers=h)
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names == ["retrieval_started", "sources", "token", "citations", "done"]
    assert events[2][1]["delta"] == NO_ANSWER_TEXT
    assert events[-1][1]["no_answer"] is True
    assert events[-1][1]["grounding"] == "documents"
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
        async def stream(  # type: ignore[no-untyped-def]
            self, *, model: str, messages: list[dict[str, str]], reasoning_effort: str | None = None
        ):
            if False:
                yield  # Make this an async generator
            raise UpstreamError(detail="<html>502 Bad Gateway</html>")

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FailingStreamer(),
        chunk_reader=FakeChunkReader(),
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
        chunk_reader=FakeChunkReader(),
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


async def test_small_talk_skips_retrieval(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
    fake_streamer: FakeStreamer,
) -> None:
    """CHAT-3 Phase-1 router: "Hi" is classified as conversational, so the SSE
    flow skips retrieval_started/sources entirely and the retriever is never
    invoked; only token/citations/done frames go out."""
    class FailingRetriever:
        async def __call__(  # type: ignore[no-untyped-def]
            self, session, ctx, workspace_id, query, top_k=8
        ):
            pytest.fail("retriever must not be called for a conversational turn")

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FailingRetriever(),
        llm_streamer=fake_streamer,
        chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "Hi"}, headers=h)
    assert r.status_code == 200
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert "retrieval_started" not in names
    assert "sources" not in names
    assert names[0] == "token"
    assert names[-1] == "done"
    assert names == ["token"] * (len(names) - 2) + ["citations", "done"]

    answer = "".join(d["delta"] for e, d in events if e == "token")
    assert answer == "".join(fake_streamer.deltas)
    done = events[-1][1]
    assert done["no_answer"] is False

    # No sources -> no data blocks in the prompt sent to the LLM, and a
    # short conversational system prompt instead of the retrieval one.
    sent = fake_streamer.calls[0]["messages"]
    assert sent[0]["content"].startswith("You are Ragz, the assistant for this document")  # type: ignore[index]
    assert "<data" not in sent[-1]["content"]  # type: ignore[index]
    assert sent[-1]["content"] == "Hi"  # type: ignore[index]

    # Persisted with no citations.
    assistant = next(
        m for m in (await session.execute(select(Message))).scalars()
        if m.role == "assistant"
    )
    assert assistant.content == answer
    cits = list((await session.execute(select(Citation))).scalars())
    assert cits == []


async def test_runtime_error_mid_stream_yields_generic_message_and_closes(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """RuntimeError mid-stream yields generic error message and terminates cleanly."""
    from ragz.modules.chat.llm import LLMDelta

    class MidStreamFailingStreamer:
        async def stream(  # type: ignore[no-untyped-def]
            self, *, model: str, messages: list[dict[str, str]], reasoning_effort: str | None = None
        ):
            yield LLMDelta("partial")
            raise RuntimeError("unexpected failure")

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=MidStreamFailingStreamer(),
        chunk_reader=FakeChunkReader(),
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


async def test_weak_retrieval_general_knowledge_fallback(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
) -> None:
    """Design §1: weak results + fallback_policy=general_knowledge (the default)
    -> answer WITHOUT document context: no sources/citations frames, done
    carries grounding='general', Message row persists it, zero citations."""
    fake = FakeStreamer(deltas=["ISO 45001 is ", "an OHS standard."])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=fake, chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is ISO 45001?"}, headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "sources" not in names and "citations" not in names
    done = next(d for n, d in frames if n == "done")
    assert done["grounding"] == "general" and done["no_answer"] is False
    # The prompt carried NO data blocks and used the GK system prompt.
    sent = fake.calls[-1]["messages"]
    assert all("<data" not in m["content"] for m in sent)  # type: ignore[index]
    # Persisted row carries the flag; no citations rows.
    msg = (
        await session.execute(
            select(Message).where(Message.id == UUID(done["message_id"]))
        )
    ).scalar_one()
    assert msg.grounding == "general"
    assert (
        await session.execute(select(Citation).where(Citation.message_id == msg.id))
    ).scalar_one_or_none() is None


async def test_weak_retrieval_decline_workspace_unchanged(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
) -> None:
    """decline workspaces keep the exact pre-Plan-I no-answer contract:
    sources frame present, NO_ANSWER_TEXT streamed, done.no_answer=True,
    grounding='documents'."""
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        r_patch = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"fallback_policy": "decline"}, headers=h_admin,
        )
        assert r_patch.status_code == 200
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is ISO 45001?"}, headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "sources" in names
    token_text = "".join(d["delta"] for n, d in frames if n == "token")
    assert token_text == NO_ANSWER_TEXT
    done = next(d for n, d in frames if n == "done")
    assert done["no_answer"] is True and done["grounding"] == "documents"


async def test_document_answer_enqueues_audit(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grounding='documents' done frame triggers the audit enqueue exactly
    once, with the persisted message's id — decline/conversational/GK turns
    must NOT (covered by the sibling test below)."""
    enqueued: list[str] = []
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_audit_message", lambda mid: enqueued.append(str(mid))
    )
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    frames = parse_sse(r.text)
    done = next(d for n, d in frames if n == "done")
    assert done["grounding"] == "documents" and done["no_answer"] is False
    assert enqueued == [done["message_id"]]


async def test_no_answer_does_not_enqueue_audit(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FakeRetriever(no_answer=True) in a decline workspace -> zero audit
    enqueues (grounding='documents' but no_answer=True carries nothing to
    audit)."""
    enqueued: list[str] = []
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_audit_message", lambda mid: enqueued.append(str(mid))
    )
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r_patch = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"fallback_policy": "decline"}, headers=h,
        )
        assert r_patch.status_code == 200
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "quantum llamas?"}, headers=h)
    frames = parse_sse(r.text)
    done = next(d for n, d in frames if n == "done")
    assert done["no_answer"] is True and done["grounding"] == "documents"
    assert enqueued == []


async def test_strict_mode_passing_answer_streams_once_unflagged(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
) -> None:
    """Design D4/§3: a strict_mode workspace with a designated utility model
    engages the Gatekeeper. A passing verdict streams the WHOLE synthesized
    answer as a SINGLE token frame (not incremental deltas -- validating
    before display requires the full text first) and validation_failed stays
    False end to end (wire + persisted row)."""
    completer = FakeCompleter([
        LLMCompletion(
            text="Revenue was 12M [1].", tool_calls=[],
            usage=LLMUsage(prompt_tokens=20, completion_tokens=5),
        ),
        LLMCompletion(
            text='{"passed": true, "critique": ""}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=15, completion_tokens=3),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"strict_mode": True}, headers=h,
        )
        assert r_ws.status_code == 200
        h_super = await auth(client, "root@platform.example")
        r_model = await client.post(
            "/api/v1/admin/models",
            json={"litellm_model_name": "utility-model", "display_name": "Utility",
                  "provider_kind": "ollama", "base_url": "http://ollama:11434"},
            headers=h_super,
        )
        r_patch = await client.patch(
            f"/api/v1/admin/models/{r_model.json()['id']}",
            json={"is_utility": True}, headers=h_super,
        )
        assert r_patch.status_code == 200
        await _disable_generative_ui(client, h_super)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    events = parse_sse(r.text)
    token_frames = [d for e, d in events if e == "token"]
    assert len(token_frames) == 1  # the whole answer arrives as ONE frame
    assert token_frames[0]["delta"] == "Revenue was 12M [1]."
    done = next(d for e, d in events if e == "done")
    assert done["validation_failed"] is False
    assert [c["model"] for c in completer.calls] == ["llama3", "utility-model"]

    msg = (
        await session.execute(select(Message).where(Message.id == UUID(done["message_id"])))
    ).scalar_one()
    assert msg.validation_failed is False


async def test_strict_mode_failing_answer_regenerates_and_flags(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
) -> None:
    """A failing verdict triggers exactly ONE critique-guided regeneration. The
    client only ever sees the SECOND synth's text (never the rejected first
    draft) and validation_failed is True on both the wire and the persisted
    row -- "not re-verified", not "known wrong" (design §3). completer.calls
    has exactly 3 entries: synth1, judge, synth2 -- no second judge call."""
    completer = FakeCompleter([
        LLMCompletion(
            text="Revenue was 12M [1].", tool_calls=[],
            usage=LLMUsage(prompt_tokens=20, completion_tokens=5),
        ),
        LLMCompletion(
            text='{"passed": false, "critique": "not grounded enough"}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=15, completion_tokens=3),
        ),
        LLMCompletion(
            text="Revenue was actually 12M, per [1].", tool_calls=[],
            usage=LLMUsage(prompt_tokens=25, completion_tokens=8),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"strict_mode": True}, headers=h,
        )
        assert r_ws.status_code == 200
        h_super = await auth(client, "root@platform.example")
        r_model = await client.post(
            "/api/v1/admin/models",
            json={"litellm_model_name": "utility-model", "display_name": "Utility",
                  "provider_kind": "ollama", "base_url": "http://ollama:11434"},
            headers=h_super,
        )
        r_patch = await client.patch(
            f"/api/v1/admin/models/{r_model.json()['id']}",
            json={"is_utility": True}, headers=h_super,
        )
        assert r_patch.status_code == 200
        await _disable_generative_ui(client, h_super)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    events = parse_sse(r.text)
    token_frames = [d for e, d in events if e == "token"]
    assert len(token_frames) == 1
    assert token_frames[0]["delta"] == "Revenue was actually 12M, per [1]."
    done = next(d for e, d in events if e == "done")
    assert done["validation_failed"] is True
    assert len(completer.calls) == 3

    msg = (
        await session.execute(select(Message).where(Message.id == UUID(done["message_id"])))
    ).scalar_one()
    assert msg.validation_failed is True
    assert msg.content == "Revenue was actually 12M, per [1]."


async def test_strict_mode_without_utility_model_streams_normally(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: User, seeded_superadmin: User, session: AsyncSession,
) -> None:
    """strict_mode=True with NO designated utility model is inert: the
    Gatekeeper never engages (use_gatekeeper requires all three of strict_mode,
    a completer, and a utility model), so the document-answer branch streams
    incremental deltas exactly as it did pre-Plan-J, at zero extra completer
    spend (no wasted synth/judge calls for an inert strict_mode toggle)."""
    fake_streamer = FakeStreamer()
    completer = FakeCompleter([])
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=fake_streamer,
        chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"strict_mode": True}, headers=h,
        )
        assert r_ws.status_code == 200
        h_super = await auth(client, "root@platform.example")
        await _disable_generative_ui(client, h_super)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    events = parse_sse(r.text)
    token_frames = [d for e, d in events if e == "token"]
    assert len(token_frames) == len(fake_streamer.deltas) == 2  # incremental, not one frame
    assert "".join(t["delta"] for t in token_frames) == "Revenue was 12M [1]."
    done = next(d for e, d in events if e == "done")
    assert done["validation_failed"] is False
    assert completer.calls == []  # no extra spend for an inert strict_mode


# --- Task 9: rolling-summary chat memory orchestration (spec §5) ------------


async def _seed_linear_history(session: AsyncSession, chat: Chat, turns: int) -> list[Message]:
    """Seed `turns` alternating user/assistant messages as one linear chain
    (turn 0 = user root, ... last turn = assistant so a real new user message
    can attach as its child via the normal active-leaf append path). Returns
    the seeded messages oldest -> newest."""
    seeded: list[Message] = []
    parent: Message | None = None
    for i in range(turns):
        msg = Message(
            chat_id=chat.id, parent_message_id=parent.id if parent else None,
            sibling_index=0, role="user" if i % 2 == 0 else "assistant",
            content=f"turn {i}",
        )
        session.add(msg)
        await session.flush()
        parent = msg
        seeded.append(msg)
    await session.commit()
    return seeded


async def _usage_record_count(session: AsyncSession, *, feature: str) -> int:
    rows = (
        await session.execute(select(UsageRecord).where(UsageRecord.feature == feature))
    ).scalars()
    return len(list(rows))


_LONG_HISTORY_TURNS = 22  # > _SUMMARY_TRIGGER_TURNS (20) ancestors once a new turn is sent
_SHORT_HISTORY_TURNS = 4  # well below the trigger


async def test_stream_reply_folds_summary_after_threshold_turns(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User, utility_model: Model,
) -> None:
    """Spec §5: once the path exceeds _SUMMARY_TRIGGER_TURNS ancestors AND a
    utility model is designated, stream_reply folds the older turns into a
    stored rolling summary and anchors it at the newest folded message."""
    completer = FakeCompleter([
        LLMCompletion(
            text='{"summary": "Folded summary of the early turns."}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id), llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(), llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        await _seed_linear_history(session, chat, _LONG_HISTORY_TURNS)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    await session.refresh(chat)
    assert chat.summary == "Folded summary of the early turns."
    assert chat.summary_upto_message_id is not None
    # The anchor is one of the seeded ancestors, not the new turn itself.
    rows = await session.execute(select(Message).where(Message.chat_id == chat.id))
    seeded_ids = {m.id for m in rows.scalars()}
    assert chat.summary_upto_message_id in seeded_ids


async def test_stream_reply_skips_summary_below_threshold(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User, utility_model: Model,
) -> None:
    """A short path (<= _SUMMARY_TRIGGER_TURNS ancestors) never folds, even
    with a completer and a designated utility model available."""
    completer = FakeCompleter([
        LLMCompletion(
            text='{"summary": "should never be used"}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=5, completion_tokens=5),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id), llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(), llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        await _seed_linear_history(session, chat, _SHORT_HISTORY_TURNS)
        h_super = await auth(client, "root@platform.example")
        await _disable_generative_ui(client, h_super)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    await session.refresh(chat)
    assert chat.summary is None
    assert completer.calls == []  # no fold-in call spent at all


async def test_stream_reply_falls_back_to_truncation_without_utility_model(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """No designated utility model (note: no `utility_model` fixture here) ->
    today's cannonball/drop truncation path, unchanged, even with a long
    history and a completer both present."""
    completer = FakeCompleter([
        LLMCompletion(
            text='{"summary": "should never be used"}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=5, completion_tokens=5),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id), llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(), llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        await _seed_linear_history(session, chat, _LONG_HISTORY_TURNS)
        h_super = await auth(client, "root@platform.example")
        await _disable_generative_ui(client, h_super)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    await session.refresh(chat)
    assert chat.summary is None
    assert completer.calls == []  # no utility model -> fold-in never even attempted


async def test_stream_reply_invalidates_summary_on_fork_above_anchor(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User, utility_model: Model,
) -> None:
    """A regenerate/edit that forks ABOVE the summary's anchor point makes the
    stored summary describe a conversation path we're no longer on. It must
    not be silently reused (spec §5): the next turn on the new branch clears
    it (or recomputes against the new path) rather than trusting it."""
    completer = FakeCompleter([
        LLMCompletion(
            text='{"summary": "First folded summary."}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20),
        ),
        LLMCompletion(
            text='{"summary": "should not be reached on a short forked branch"}',
            tool_calls=[], usage=LLMUsage(prompt_tokens=5, completion_tokens=5),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id), llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(), llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        seeded = await _seed_linear_history(session, chat, _LONG_HISTORY_TURNS)
        r1 = await client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "what was revenue?"}, headers=h)
        assert r1.status_code == 200
        await session.refresh(chat)
        old_anchor = chat.summary_upto_message_id
        assert old_anchor is not None

        # Fork ABOVE the anchor: an early assistant ancestor (seeded[1]) becomes
        # the explicit parent of a brand-new sibling branch that never passes
        # through the anchor message at all.
        fork_parent = next(m for m in seeded if m.role == "assistant")
        r2 = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "a totally different question",
                  "parent_message_id": str(fork_parent.id)},
            headers=h,
        )
        assert r2.status_code == 200
    await session.refresh(chat)
    assert chat.summary_upto_message_id != old_anchor or chat.summary is None


async def test_summary_fold_in_is_metered_as_chat_usage(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User, utility_model: Model,
) -> None:
    """The fold-in call is metered as feature="chat" quota usage (a live,
    in-request chat cost) -- NOT "ingestion". A triggered fold produces TWO
    "chat" usage records: the fold-in's own, plus the answer's."""
    completer = FakeCompleter([
        LLMCompletion(
            text='{"summary": "Folded summary."}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id), llm_streamer=FakeStreamer(),
        chunk_reader=FakeChunkReader(), llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        await _seed_linear_history(session, chat, _LONG_HISTORY_TURNS)
        before = await _usage_record_count(session, feature="chat")
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "what was revenue?"}, headers=h)
        assert r.status_code == 200
    after = await _usage_record_count(session, feature="chat")
    assert after > before + 1  # the answer's own record_usage PLUS the fold-in's
    assert await _usage_record_count(session, feature="ingestion") == 0


async def test_stream_reply_folds_summary_in_conversational_branch_too(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User, utility_model: Model,
    fake_streamer: FakeStreamer,
) -> None:
    """The conversational (small-talk) branch shares the same _assemble_history
    orchestration as the retrieval branch -- a long history still folds even
    when the NEW turn itself is classified conversational."""
    completer = FakeCompleter([
        LLMCompletion(
            text='{"summary": "Folded summary from small talk turn."}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id), llm_streamer=fake_streamer,
        chunk_reader=FakeChunkReader(), llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, "a@acme.com")
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        await _seed_linear_history(session, chat, _LONG_HISTORY_TURNS)
        r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                              json={"content": "Hi"}, headers=h)
    assert r.status_code == 200
    await session.refresh(chat)
    assert chat.summary == "Folded summary from small talk turn."


async def test_send_message_rejects_reasoning_effort_on_unsupported_model(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    """The model created by make_model_and_chat defaults to
    supports_reasoning=False -- requesting a real tier on it must 409 before
    any SSE bytes are sent, same as the model_id resolution checks above it."""
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "hi", "reasoning_effort": "high"},
        headers=h,
    )
    assert r.status_code == 409


async def test_send_message_accepts_reasoning_effort_on_supported_model(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    h_super = await auth(chat_client, "root@platform.example")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    model_id = (
        await session.execute(select(Model).where(Model.litellm_model_name == "llama3"))
    ).scalar_one().id
    r_patch = await chat_client.patch(
        f"/api/v1/admin/models/{model_id}",
        json={"supports_reasoning": True}, headers=h_super,
    )
    assert r_patch.status_code == 200
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "hi", "reasoning_effort": "high"},
        headers=h,
    )
    assert r.status_code == 200
    assert fake_streamer.calls[-1]["reasoning_effort"] == "high"


async def test_send_message_off_is_always_accepted(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    """"off"/absent never actually requests reasoning, so it must never
    conflict, even on a model with supports_reasoning=False."""
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "hi", "reasoning_effort": "off"},
        headers=h,
    )
    assert r.status_code == 200
    assert fake_streamer.calls[-1]["reasoning_effort"] is None


async def test_general_knowledge_fallback_threads_existing_summary(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    session: AsyncSession, seeded_user: User, seeded_superadmin: User,
) -> None:
    """Regression (Plan K Task 9 gap): the general-knowledge fallback branch
    must consult the SAME rolling summary as build_messages/
    build_conversational_messages (spec §5). A fact folded into chat.summary
    from turns that fell out of the raw prompt must still reach the model
    when retrieval misses and the workspace's default
    fallback_policy=general_knowledge kicks in -- not just when
    build_general_knowledge_messages is called directly with summary=... in
    isolation."""
    fake = FakeStreamer(deltas=["Your name is Priya."])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=fake, chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        chat = await session.get(Chat, UUID(chat_id))
        assert chat is not None
        seeded = await _seed_linear_history(session, chat, _SHORT_HISTORY_TURNS)
        # A pre-existing, valid rolling summary anchored at the newest seeded
        # message -- exactly the shape _assemble_history hands back untouched
        # when the path is still short (no completer needed for this test).
        chat.summary = (
            "The user's name is Priya and the project is called Project Nighthawk."
        )
        chat.summary_upto_message_id = seeded[-1].id
        await session.commit()
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What's my name?"}, headers=h,
        )
    assert r.status_code == 200
    done = next(d for n, d in parse_sse(r.text) if n == "done")
    assert done["grounding"] == "general"  # confirms we actually hit the fallback branch
    sent = fake.calls[-1]["messages"]
    assert any("Priya" in m["content"] for m in sent)  # type: ignore[index]


# --- ChatCreate.first_message + POST /messages/{id}/answer -------------------
# The opening turn used to live only in the browser between "chat created" and
# "message sent": a reload in that window lost it and left an empty chat behind.
# These cover the atomic-create half and the resumable-answer half.


async def test_create_chat_persists_first_message_without_answering_it(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)

    r = await chat_client.post(
        "/api/v1/chats",
        json={
            "workspace_id": str(chat_env["workspace"].id),
            "first_message": "what was revenue?",
        },
        headers=h,
    )
    assert r.status_code == 201
    chat_id = UUID(r.json()["id"])

    rows = (
        await session.execute(select(Message).where(Message.chat_id == chat_id))
    ).scalars().all()
    # Persisted immediately, and NOT answered -- create doesn't stream.
    assert [(m.role, m.content) for m in rows] == [("user", "what was revenue?")]


async def test_answer_streams_the_reply_to_a_persisted_first_message(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    r = await chat_client.post(
        "/api/v1/chats",
        json={
            "workspace_id": str(chat_env["workspace"].id),
            "first_message": "what was revenue?",
        },
        headers=h,
    )
    chat_id = UUID(r.json()["id"])
    user_msg = (
        await session.execute(select(Message).where(Message.chat_id == chat_id))
    ).scalars().one()

    r = await chat_client.post(f"/api/v1/messages/{user_msg.id}/answer", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    names = [n for n, _ in parse_sse(r.text)]
    assert names[0] == "retrieval_started"
    assert names[-1] == "done"

    session.expire_all()
    rows = (
        await session.execute(select(Message).where(Message.chat_id == chat_id))
    ).scalars().all()
    roles = sorted(m.role for m in rows)
    assert roles == ["assistant", "user"]
    reply = next(m for m in rows if m.role == "assistant")
    assert reply.parent_message_id == user_msg.id  # answered THAT turn, not a new root


async def test_answer_refuses_a_second_answer_and_a_non_user_message(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "what was revenue?"}, headers=h)
    assert r.status_code == 200
    rows = (
        await session.execute(select(Message).where(Message.chat_id == UUID(chat_id)))
    ).scalars().all()
    user_msg = next(m for m in rows if m.role == "user")
    assistant_msg = next(m for m in rows if m.role == "assistant")

    # Already answered -> refuse, so a reload racing the first stream can't
    # fork a duplicate reply under the same question.
    r = await chat_client.post(f"/api/v1/messages/{user_msg.id}/answer", headers=h)
    assert r.status_code == 409
    # An assistant message is regenerate's job, not answer's.
    r = await chat_client.post(f"/api/v1/messages/{assistant_msg.id}/answer", headers=h)
    assert r.status_code == 409
