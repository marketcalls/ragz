from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.core.config import Settings
from raghub.modules.auth.models import User
from raghub.modules.chat.service import NO_ANSWER_TEXT
from raghub.modules.retrieval.service import RetrievalResult, RetrievedChunk
from tests.api.test_chat_budget_override import make_client
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import FakeChunkReader, FakeStreamer, _stub_litellm_handler  # noqa: F401


class SequenceRetriever:
    """Returns scripted results turn by turn (turn 1 rich, turn 2 empty, ...)."""

    def __init__(self, results: list[RetrievalResult]) -> None:
        self.results = list(results)

    async def __call__(self, session, ctx, workspace_id, query, top_k=None):  # type: ignore[no-untyped-def]
        return self.results.pop(0)


@pytest.fixture
def fake_streamer() -> FakeStreamer:
    return FakeStreamer()  # answers "Revenue was 12M [1]." -> cites marker 1


async def test_followup_with_empty_retrieval_backfills_previous_citations(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    doc_id = chat_env["document"].id
    cited = RetrievedChunk(document_id=doc_id, page=3, chunk_index=0,
                           text="Revenue was 12M.", score=0.91)
    retriever = SequenceRetriever([
        RetrievalResult(chunks=[cited], no_answer=False),      # turn 1
        RetrievalResult(chunks=[], no_answer=True),            # turn 2: nothing
    ])
    reader = FakeChunkReader()
    ref = f"{doc_id}:3:0"
    reader.chunks_by_ref[ref] = RetrievedChunk(
        document_id=doc_id, page=3, chunk_index=0, text="Revenue was 12M.", score=0.0
    )
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer, chunk_reader=reader) as c:  # type: ignore[arg-type]
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r1 = await c.post(f"/api/v1/chats/{chat_id}/messages",
                          json={"content": "what was revenue?"}, headers=h)
        assert r1.status_code == 200
        # Turn 1 has no previous assistant turn -> backfill never queried.
        assert reader.ref_calls == []

        r2 = await c.post(f"/api/v1/chats/{chat_id}/messages",
                          json={"content": "expand point 2"}, headers=h)
        events = parse_sse(r2.text)
    # The previous turn's citation was replayed through the tenant-filtered path
    assert reader.ref_calls == [[ref]]
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert [s["document_id"] for s in sources] == [str(doc_id)]
    assert sources[0]["score"] == 0.0  # backfilled, not a fresh hit
    done = next(d for e, d in events if e == "done")
    assert done["no_answer"] is False  # refusal suppressed: we HAVE grounding
    tokens = "".join(d["delta"] for e, d in events if e == "token")
    assert NO_ANSWER_TEXT not in tokens


async def test_no_previous_citations_still_refuses_on_empty_retrieval(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
) -> None:
    retriever = SequenceRetriever([RetrievalResult(chunks=[], no_answer=True)])
    reader = FakeChunkReader()
    async with make_client(engine, redis_client, test_settings,
                           retriever, fake_streamer, chunk_reader=reader) as c:  # type: ignore[arg-type]
        h = await auth(c, "a@acme.com")
        chat_id = await make_model_and_chat(c, chat_env, session, seeded_superadmin, h)
        r = await c.post(f"/api/v1/chats/{chat_id}/messages",
                         json={"content": "anything?"}, headers=h)
        events = parse_sse(r.text)
    assert reader.ref_calls == []  # nothing to backfill from
    done = next(d for e, d in events if e == "done")
    assert done["no_answer"] is True  # CHAT-9 refusal path intact
