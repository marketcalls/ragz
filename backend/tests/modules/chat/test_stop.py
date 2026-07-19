import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.core.config import Settings
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.chat import service
from raghub.modules.chat.llm import LLMDelta, LLMUsage
from raghub.modules.chat.models import Chat, Message
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization, Workspace

SETTINGS = Settings(_env_file=None)


class SlowStreamer:
    """Yields deltas one event-loop turn at a time so the consumer can abort mid-stream."""

    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas
        self.closed = False

    async def stream(
        self, *, model: str, messages: list[dict[str, str]]
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
    session: AsyncSession, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1, finding 1: a cancellation that lands AFTER
    _persist_assistant has already committed the row (but before the
    subsequent record_usage/citations_event/done_event awaits/yields) must
    NOT persist a second assistant row via persist_stopped_detached. Forces
    that exact window by making quota_service.record_usage raise
    CancelledError once the stream has completed normally and the row is
    already committed.

    This is the regression test for the bug: without `streamed_parts.clear()`
    immediately after the successful persist, `streamed_parts` is still
    truthy when the CancelledError handler runs, so a second (stopped) row
    would be inserted under the same parent alongside the first (non-stopped)
    row.
    """
    ctx, chat, user_msg, chat_ws = await _seed(session)
    factory = build_session_factory(engine)
    streamer = SlowStreamer(["Hi"])

    async def _boom_after_completed_stream(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(service.quota_service, "record_usage", _boom_after_completed_stream)

    agen = service.stream_reply(
        session, ctx, chat=chat, workspace=chat_ws, user_message=user_msg,
        model=_FakeModel(),  # type: ignore[arg-type]
        streamer=streamer, retriever=_never_retrieve,  # type: ignore[arg-type]
        chunk_reader=_NeverChunkReader(),  # type: ignore[arg-type]
        settings=SETTINGS, session_factory=factory,
    )
    with pytest.raises(asyncio.CancelledError):
        async for _event in agen:
            pass

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
