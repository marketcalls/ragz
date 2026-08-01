from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.chat.prompting import CANNONBALL_MARKER, SYSTEM_PROMPT
from ragz.modules.retrieval.service import RetrievedChunk
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import FakeChunkReader, FakeRetriever, FakeStreamer, _stub_litellm_handler


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()


def make_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    retriever: FakeRetriever, fake_streamer: FakeStreamer,
    chunk_reader: FakeChunkReader | None = None,
) -> httpx.AsyncClient:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=retriever,
        llm_streamer=fake_streamer,
        chunk_reader=chunk_reader if chunk_reader is not None else FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_workspace_override_lands_after_base_system_prompt(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    chat_env["workspace"].system_prompt_override = "Always answer in pirate voice."
    await session.commit()
    async with make_client(engine, redis_client, test_settings,
                           FakeRetriever(chat_env["document"].id), fake_streamer) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "what was revenue?"}, headers=h)
        assert r.status_code == 200
    system = str(fake_streamer.calls[0]["messages"][0]["content"])
    assert system.startswith(SYSTEM_PROMPT)
    assert "Always answer in pirate voice." in system


async def test_oversized_source_is_trimmed_from_sources_frame_and_prompt(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    doc_id = chat_env["document"].id
    retriever = FakeRetriever(doc_id)
    retriever.chunks = [
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=0,
                       text="Revenue was 12M.", score=0.9),
        # ~9000 tokens: cannot fit the default 8000-budget sources share (5600)
        RetrievedChunk(document_id=doc_id, page=2, chunk_index=1,
                       text="filler " * 9000, score=0.5),
    ]
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "what was revenue?"}, headers=h)
        events = parse_sse(r.text)
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert [s["marker"] for s in sources] == [1]  # the giant chunk was dropped
    prompt_user = str(fake_streamer.calls[0]["messages"][-1]["content"])
    assert 'id="1"' in prompt_user and 'id="2"' not in prompt_user


async def test_lone_oversized_source_is_cannonballed_not_dropped(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    doc_id = chat_env["document"].id
    retriever = FakeRetriever(doc_id)
    retriever.chunks = [
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=0,
                       text="HEAD marker. " + "filler " * 9000 + " TAIL marker.",
                       score=0.9),
    ]
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "what was revenue?"}, headers=h)
        events = parse_sse(r.text)
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert [s["marker"] for s in sources] == [1]
    prompt_user = str(fake_streamer.calls[0]["messages"][-1]["content"])
    assert CANNONBALL_MARKER in prompt_user
    assert "HEAD marker." in prompt_user and "TAIL marker." in prompt_user
