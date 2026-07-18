from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.core.config import Settings
from raghub.modules.auth.models import User
from raghub.modules.chat.service import NO_ANSWER_TEXT
from raghub.modules.documents.models import Document
from raghub.modules.retrieval.service import RetrievedChunk
from tests.api.test_chat_budget_override import make_client
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import FakeChunkReader, FakeRetriever, FakeStreamer


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()


async def seed_pinned_doc(
    session: AsyncSession, seeded_user: User, chat_env: dict[str, Any],
    reader: FakeChunkReader,
) -> Document:
    doc = Document(org_id=seeded_user.org_id, workspace_id=chat_env["workspace"].id,
                   filename="policy.pdf", mime="application/pdf", size_bytes=10,
                   content_hash="hp", status="indexed", storage_key="kp",
                   created_by=seeded_user.id, pinned=True)
    session.add(doc)
    await session.commit()
    reader.document_chunks[doc.id] = [
        RetrievedChunk(document_id=doc.id, page=1, chunk_index=0,
                       text="Policy: refunds within 30 days.", score=1.0),
    ]
    return doc


async def test_pinned_chunks_lead_the_sources_with_unified_markers(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    reader = FakeChunkReader()
    pinned_doc = await seed_pinned_doc(session, seeded_user, chat_env, reader)
    async with make_client(engine, redis_client, test_settings,
                           FakeRetriever(chat_env["document"].id), fake_streamer,
                           chunk_reader=reader) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "what was revenue?"}, headers=h)
        events = parse_sse(r.text)
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert sources[0]["document_id"] == str(pinned_doc.id)
    assert sources[0]["marker"] == 1 and sources[0]["score"] == 1.0
    # retrieved chunks follow with continuing markers
    assert [s["marker"] for s in sources] == list(range(1, len(sources) + 1))
    assert str(chat_env["document"].id) in {s["document_id"] for s in sources}
    prompt_user = str(fake_streamer.calls[0]["messages"][-1]["content"])
    assert "Policy: refunds within 30 days." in prompt_user


async def test_no_answer_refusal_suppressed_when_pinned_docs_exist(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    reader = FakeChunkReader()
    await seed_pinned_doc(session, seeded_user, chat_env, reader)
    retriever = FakeRetriever(chat_env["document"].id, no_answer=True)
    retriever.chunks = []
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer, chunk_reader=reader) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "refund policy?"}, headers=h)
        events = parse_sse(r.text)
    done = next(d for e, d in events if e == "done")
    assert done["no_answer"] is False
    tokens = "".join(d["delta"] for e, d in events if e == "token")
    assert tokens != NO_ANSWER_TEXT  # streamed a real (fake) LLM answer
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert len(sources) == 1  # the pinned chunk carried the turn
