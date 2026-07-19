"""Escalated chat turns: agent_step frames, summed usage, non-regression pins."""

from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.api.app import create_app
from raghub.core.config import Settings, get_settings
from raghub.core.db import build_session_factory
from raghub.modules.chat.llm import LLMCompletion, LLMUsage
from raghub.modules.chat.service import NO_ANSWER_TEXT
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import (
    FakeChunkReader,
    FakeCompleter,
    FakeRetriever,
    FakeStreamer,
    _stub_litellm_handler,
)


def _search_completion(query: str) -> LLMCompletion:
    return LLMCompletion(
        text=f'{{"action": "search", "query": "{query}"}}', tool_calls=[],
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )


async def test_multi_part_question_escalates(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    completer = FakeCompleter([_search_completion("muster point")])
    fake_streamer = FakeStreamer()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=fake_streamer, chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        # Flag the model so the JSON planner runs (deterministic with FakeCompleter):
        h_super = await auth(client, "root@platform.example")
        models = (await client.get("/api/v1/admin/models", headers=h_super)).json()
        await client.patch(
            f"/api/v1/admin/models/{models[0]['id']}",
            json={"tools_unreliable": True}, headers=h_super,
        )
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is the muster point and when was it approved?"},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    # agent_step lands between retrieval_started and sources:
    assert names.index("retrieval_started") < names.index("agent_step") < names.index("sources")
    step = next(d for n, d in frames if n == "agent_step")
    assert step == {"n": 1, "tool": "search", "query": "muster point"}
    done = next(d for n, d in frames if n == "done")
    # Usage summed: synthesize (42/7 from FakeStreamer) + planner rounds (10+3 / 5+1):
    assert done["prompt_tokens"] == 55 and done["completion_tokens"] == 13
    assert done["grounding"] == "documents"
    citations = next(d for n, d in frames if n == "citations")
    assert citations["citations"]  # "[1]." from FakeStreamer still resolves


async def test_simple_question_never_escalates(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """Load-test non-regression pin (design §9): same app WITH a completer,
    plain single-interrogative question -> zero planner calls, no agent_step
    frame, stream identical to the pre-Plan-I contract."""
    completer = FakeCompleter([_search_completion("muster point")])
    fake_streamer = FakeStreamer()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=fake_streamer, chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is the muster point?"},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "agent_step" not in names
    assert completer.calls == []
    done = next(d for n, d in frames if n == "done")
    # Byte-identical to the pre-Plan-I contract: only the synthesize usage.
    assert done["prompt_tokens"] == 42 and done["completion_tokens"] == 7
    assert done["grounding"] == "documents"


async def test_weak_results_trigger_loop_before_gk_fallback(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """Post-retrieval trigger: FakeRetriever(no_answer=True), workspace has an
    indexed doc (chat_env's default document is already status="indexed"),
    plain question. Completer scripts one search whose (scripted) second
    FakeRetriever response grounds -> loop rescues the turn: agent_step
    present, done grounding == 'documents'."""

    class _FlippingRetriever(FakeRetriever):
        """Weak on the FIRST call (the single retrieval shot); every call
        after that (i.e. the loop's own search) rescues the turn."""

        async def __call__(  # type: ignore[no-untyped-def]
            self, session, ctx, workspace_id, query, top_k=None, metadata_clauses=None
        ):
            result = await super().__call__(
                session, ctx, workspace_id, query, top_k=top_k, metadata_clauses=metadata_clauses
            )
            self.no_answer = False
            return result

    retriever = _FlippingRetriever(chat_env["document"].id, no_answer=True)
    completer = FakeCompleter([_search_completion("muster point")])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=retriever, llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is the muster point?"},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "agent_step" in names
    done = next(d for n, d in frames if n == "done")
    assert done["grounding"] == "documents"
    citations = next(d for n, d in frames if n == "citations")
    assert citations["citations"]


async def test_weak_results_in_decline_workspace_do_not_loop(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """PATCH the workspace to decline; FakeRetriever(no_answer=True); plain
    question; completer injected -> completer.calls == [], NO_ANSWER_TEXT
    streamed (Task 2 contract untouched)."""
    completer = FakeCompleter([_search_completion("muster point")])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer,
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
            json={"content": "What is the muster point?"},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "agent_step" not in names
    assert completer.calls == []
    token_text = "".join(d["delta"] for n, d in frames if n == "token")
    assert token_text == NO_ANSWER_TEXT
    done = next(d for n, d in frames if n == "done")
    assert done["no_answer"] is True and done["grounding"] == "documents"
