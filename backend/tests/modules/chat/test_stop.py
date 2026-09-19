import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.core.config import Settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.chat import service
from ragz.modules.chat.llm import LLMDelta, LLMUsage
from ragz.modules.chat.models import Chat, Message
from ragz.modules.quotas.models import UsageRecord
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization, Workspace

SETTINGS = Settings(_env_file=None)


class SlowStreamer:
    """Yields deltas one event-loop turn at a time so the consumer can abort mid-stream."""

    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas
        self.closed = False

    async def stream(
        self, *, model: str, messages: list[dict[str, str]], reasoning_effort: str | None = None
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        try:
            for d in self.deltas:
                await asyncio.sleep(0)
                yield LLMDelta(text=d)
            yield LLMUsage(prompt_tokens=1, completion_tokens=len(self.deltas))
        except (asyncio.CancelledError, GeneratorExit):
            self.closed = True
            raise


async def _seed(session: AsyncSession) -> tuple[TenantContext, Chat, Message, Workspace]:
    org = Organization(name=f"org-{uuid4()}")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=f"u-{uuid4()}@t.io",
                password_hash=hash_password("pw123456"), role="user")
    ws = Workspace(org_id=org.id, name="W")
    session.add_all([user, ws])
    await session.flush()
    chat = Chat(org_id=org.id, workspace_id=ws.id, user_id=user.id)
    session.add(chat)
    await session.commit()
    ctx = TenantContext(user_id=user.id, org_id=org.id, role="user",
                        workspace_ids=frozenset({ws.id}))
    # "hello" -> conversational route (router.classify_query): no retriever
    # involvement in this test. ("hello there" does NOT classify as
    # conversational under the merged router.py - _GREETING_RE matches only
    # the bare greeting token, not "hello there" - so a single-word greeting
    # is used here instead.)
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content="hello", parent=None
    )
    return ctx, chat, user_msg, ws


class _FakeModel:
    """stream_reply only reads .id and .litellm_model_name."""
    def __init__(self) -> None:
        self.id = None  # model_id nullable on Message; None avoids FK setup
        self.litellm_model_name = "fake"


async def _never_retrieve(*args: object, **kwargs: object) -> None:
    raise AssertionError("conversational path must not retrieve")


class _NeverChunkReader:
    """stream_reply's conversational branch never touches chunk_reader; any
    call here is a test bug."""

    async def list_document_chunks(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("conversational path must not read chunks")

    async def get_chunks_by_refs(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("conversational path must not read chunks")


async def test_abort_mid_stream_persists_partial(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    ctx, chat, user_msg, chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    streamer = SlowStreamer(["Hel", "lo ", "wor", "ld"])
    agen = service.stream_reply(
        session, ctx, chat=chat, workspace=chat_ws, user_message=user_msg,
        model=_FakeModel(),  # type: ignore[arg-type]
        streamer=streamer, retriever=_never_retrieve,  # type: ignore[arg-type]
        chunk_reader=_NeverChunkReader(),  # type: ignore[arg-type]
        settings=SETTINGS, session_factory=factory,
    )
    tokens = 0
    async for event in agen:
        if event.event == "token":
            tokens += 1
        if tokens == 2:
            break
    await agen.aclose()  # simulates Starlette closing the generator on disconnect

    assert streamer.closed  # upstream LLM stream was actually stopped
    await asyncio.gather(*service._STOP_PERSISTS)
    row = (
        await session.execute(
            select(Message).where(Message.chat_id == chat.id,
                                  Message.role == service.ROLE_ASSISTANT)
        )
    ).scalar_one()
    assert row.stopped is True
    assert row.content == "Hello "  # exactly the streamed prefix
    assert row.parent_message_id == user_msg.id


async def test_abort_before_first_token_persists_nothing(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    ctx, chat, user_msg, chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    agen = service.stream_reply(
        session, ctx, chat=chat, workspace=chat_ws, user_message=user_msg,
        model=_FakeModel(),  # type: ignore[arg-type]
        streamer=SlowStreamer(["never"]), retriever=_never_retrieve,  # type: ignore[arg-type]
        chunk_reader=_NeverChunkReader(),  # type: ignore[arg-type]
        settings=SETTINGS, session_factory=factory,
    )
    await agen.aclose()  # closed before iteration produced any token
    await asyncio.gather(*service._STOP_PERSISTS)
    count = (
        await session.execute(
            select(Message).where(Message.chat_id == chat.id,
                                  Message.role == service.ROLE_ASSISTANT)
        )
    ).scalars().all()
    assert count == []  # user message retry-sibling semantics already cover this


async def test_late_cancel_after_persist_does_not_duplicate_row(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    """Review round 1, finding 1: a cancellation that lands AFTER
    _persist_assistant has already committed the row (but before the
    subsequent citations/done yields) must NOT persist a second assistant row
    via persist_stopped_detached. Advance to the citations frame, which is the
    first yield after the normal assistant row has committed, then close the
    stream at that exact boundary.

    This is the regression test for the bug: without `streamed_parts.clear()`
    immediately after the successful persist, `streamed_parts` is still
    truthy when the CancelledError handler runs, so a second (stopped) row
    would be inserted under the same parent alongside the first (non-stopped)
    row.
    """
    ctx, chat, user_msg, chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    streamer = SlowStreamer(["Hi"])

    agen = service.stream_reply(
        session, ctx, chat=chat, workspace=chat_ws, user_message=user_msg,
        model=_FakeModel(),  # type: ignore[arg-type]
        streamer=streamer, retriever=_never_retrieve,  # type: ignore[arg-type]
        chunk_reader=_NeverChunkReader(),  # type: ignore[arg-type]
        settings=SETTINGS, session_factory=factory,
    )
    assert (await anext(agen)).event == "token"
    assert (await anext(agen)).event == "citations"
    await agen.aclose()

    await asyncio.gather(*service._STOP_PERSISTS)
    rows = (
        await session.execute(
            select(Message).where(Message.chat_id == chat.id,
                                  Message.role == service.ROLE_ASSISTANT)
        )
    ).scalars().all()
    assert len(rows) == 1  # no duplicate row from persist_stopped_detached
    assert rows[0].stopped is False  # it's the normally-persisted row
    assert rows[0].content == "Hi"


async def test_cancel_after_durable_usage_before_message_does_not_double_charge(
    session: AsyncSession,
    engine: AsyncEngine,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    ctx, chat, user_msg, chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    persist_started = asyncio.Event()

    async def _blocked_persist(*args: object, **kwargs: object) -> Message:
        persist_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(service, "_persist_assistant", _blocked_persist)
    agen = service.stream_reply(
        session,
        ctx,
        chat=chat,
        workspace=chat_ws,
        user_message=user_msg,
        model=_FakeModel(),  # type: ignore[arg-type]
        streamer=SlowStreamer(["Hi"]),
        retriever=_never_retrieve,  # type: ignore[arg-type]
        chunk_reader=_NeverChunkReader(),  # type: ignore[arg-type]
        settings=SETTINGS,
        session_factory=factory,
    )

    async def _consume() -> None:
        async for _event in agen:
            pass

    consumer = asyncio.create_task(_consume())
    await persist_started.wait()
    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass
    await asyncio.gather(*service._STOP_PERSISTS)

    usage = list(
        (
            await session.execute(
                select(UsageRecord).where(
                    UsageRecord.org_id == ctx.org_id,
                    UsageRecord.feature == "chat",
                )
            )
        ).scalars()
    )
    assert len(usage) == 1


async def test_persist_stopped_detached_records_partial_usage(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    """Plan K carried fix: persist_stopped_detached's two new required kwargs
    (prompt_tokens/completion_tokens) get written as a UsageRecord in the SAME
    detached session/transaction as the stopped message, not a second scheme."""
    ctx, chat, user_msg, _chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    task = service.persist_stopped_detached(
        factory, ctx, chat_id=chat.id, user_message_id=user_msg.id,
        content="partial answer text", model_id=None,
        prompt_tokens=42, completion_tokens=7,
    )
    await task
    records = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.org_id == ctx.org_id, UsageRecord.feature == "chat"
            )
        )
    ).scalars().all()
    assert any(r.prompt_tokens == 42 and r.completion_tokens == 7 for r in records)


async def test_abort_mid_stream_meters_estimated_partial_usage(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    """Companion to test_abort_mid_stream_persists_partial: the same abort
    must now ALSO meter the estimated (assembled prompt + streamed partial)
    token usage — previously this path recorded zero UsageRecords."""
    ctx, chat, user_msg, chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    streamer = SlowStreamer(["Hel", "lo ", "wor", "ld"])
    agen = service.stream_reply(
        session, ctx, chat=chat, workspace=chat_ws, user_message=user_msg,
        model=_FakeModel(),  # type: ignore[arg-type]
        streamer=streamer, retriever=_never_retrieve,  # type: ignore[arg-type]
        chunk_reader=_NeverChunkReader(),  # type: ignore[arg-type]
        settings=SETTINGS, session_factory=factory,
    )
    tokens = 0
    async for event in agen:
        if event.event == "token":
            tokens += 1
        if tokens == 2:
            break
    await agen.aclose()  # simulates Starlette closing the generator on disconnect

    await asyncio.gather(*service._STOP_PERSISTS)
    records = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.org_id == ctx.org_id, UsageRecord.feature == "chat"
            )
        )
    ).scalars().all()
    # previously: zero records on this path
    assert any(r.prompt_tokens > 0 and r.completion_tokens > 0 for r in records)
