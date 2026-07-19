from typing import Any
from uuid import uuid4

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
                   created_by=seeded_user.id, pinned=True, lineage_id=uuid4())
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


async def test_huge_query_never_streams_an_answer_over_empty_sources(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    """Edge case: a user message whose token cost alone exceeds the whole
    sources share (5600) drives sources_budget to 0, so the pinned half-cap
    also drops to 0 and the pinned chunk cannot survive into kept_sources.
    The no_answer decision must key off THAT (the post-fit reality), not the
    pre-fit pinned pool -- so the retrieval no_answer verdict must stand:
    either the client gets a non-empty sources frame with a real answer, or
    it gets the no-answer refusal. It must never get an answer streamed over
    a sources frame that turned out empty."""
    reader = FakeChunkReader()
    await seed_pinned_doc(session, seeded_user, chat_env, reader)
    retriever = FakeRetriever(chat_env["document"].id, no_answer=True)
    retriever.chunks = []
    # CJK repeats stay well under the 32000-char body limit while still
    # tokenizing far denser than ASCII (~6000 tokens here, > split.sources
    # of 5600) -- "filler " * N would need >40000 chars to reach that count.
    huge_query = "填充词语 " * 750
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer, chunk_reader=reader) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        # Decline explicitly: this test is about the budget/fit edge case, not
        # Plan I's general-knowledge fallback (the workspace default since
        # Plan I), and asserts a `sources` frame is always present.
        r_patch = await c.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"fallback_policy": "decline"}, headers=h,
        )
        assert r_patch.status_code == 200
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": huge_query}, headers=h)
        events = parse_sse(r.text)
    done = next(d for e, d in events if e == "done")
    sources = next(d for e, d in events if e == "sources")["sources"]
    if done["no_answer"]:
        tokens = "".join(d["delta"] for e, d in events if e == "token")
        assert tokens == NO_ANSWER_TEXT
    else:
        assert len(sources) > 0  # a real answer streamed -> sources were non-empty


async def test_huge_query_forces_no_answer_even_when_retriever_says_answerable(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    """Sibling of test_huge_query_never_streams_an_answer_over_empty_sources,
    pinned to the specific regression: the retriever says no_answer=False and
    returns real hits, but the huge query still drives sources_budget to 0,
    so fit_sources drops every chunk and kept_sources ends up empty. The
    no-answer guard must fire on the empty fit itself, not on
    result.no_answer -- streaming an answer over an empty sources frame is
    never acceptable even when retrieval was "confident"."""
    reader = FakeChunkReader()
    retriever = FakeRetriever(chat_env["document"].id, no_answer=False)
    huge_query = "填充词语 " * 750
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer, chunk_reader=reader) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        # Decline explicitly: see the sibling test above for why.
        r_patch = await c.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"fallback_policy": "decline"}, headers=h,
        )
        assert r_patch.status_code == 200
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": huge_query}, headers=h)
        events = parse_sse(r.text)
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert sources == []  # the fit dropped every chunk
    done = next(d for e, d in events if e == "done")
    assert done["no_answer"] is True
    tokens = "".join(d["delta"] for e, d in events if e == "token")
    assert tokens == NO_ANSWER_TEXT


async def test_long_pinned_chunk_does_not_starve_retrieved_sources(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    """Starvation edge case: a pinned chunk sized between half and the full
    post-query sources budget must be excluded by the half-cap rather than
    crowding out retrieval -- with a modest query, retrieved chunks still
    make it into the rendered sources frame."""
    reader = FakeChunkReader()
    pinned_doc = await seed_pinned_doc(session, seeded_user, chat_env, reader)
    # ~3302 tokens: bigger than half of the ~5596 post-query sources budget
    # (~2798), but well under the full budget -- exactly the failure mode
    # the half-cap exists to prevent.
    reader.document_chunks[pinned_doc.id] = [
        RetrievedChunk(document_id=pinned_doc.id, page=1, chunk_index=0,
                       text="filler " * 3300, score=1.0),
    ]
    async with make_client(engine, redis_client, test_settings,
                           FakeRetriever(chat_env["document"].id), fake_streamer,
                           chunk_reader=reader) as c:
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "what was revenue?"}, headers=h)
        events = parse_sse(r.text)
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert str(pinned_doc.id) not in {s["document_id"] for s in sources}
    assert str(chat_env["document"].id) in {s["document_id"] for s in sources}
