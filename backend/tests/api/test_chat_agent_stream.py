"""Escalated chat turns: agent_step frames, summed usage, non-regression pins."""

from typing import Any
from uuid import UUID

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.chat.llm import LLMCompletion, LLMUsage
from ragz.modules.chat.models import Citation, Message
from ragz.modules.chat.service import NO_ANSWER_TEXT
from ragz.modules.chat.web import WebResult
from ragz.modules.models.models import Model
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.conftest import (
    FakeChunkReader,
    FakeCompleter,
    FakeRetriever,
    FakeStreamer,
    FakeWebSearcher,
    _stub_litellm_handler,
)


def _search_completion(query: str) -> LLMCompletion:
    return LLMCompletion(
        text=f'{{"action": "search", "query": "{query}"}}', tool_calls=[],
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )


def _web_search_completion(query: str) -> LLMCompletion:
    return LLMCompletion(
        text=f'{{"action": "web_search", "query": "{query}"}}', tool_calls=[],
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
    assert "tool_result" not in names  # non-web tools never emit tool_result
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


async def _flag_tools_unreliable(client: httpx.AsyncClient, h_super: dict[str, str]) -> None:
    # list_models orders by created_at ascending -- the LAST entry is the
    # model make_model_and_chat just created for THIS call (sub-cases in
    # test_web_search_not_offered_when_disabled_or_decline share an engine
    # and accumulate models across calls, so models[0] would be stale).
    models = (await client.get("/api/v1/admin/models", headers=h_super)).json()
    await client.patch(
        f"/api/v1/admin/models/{models[-1]['id']}",
        json={"tools_unreliable": True}, headers=h_super,
    )


async def test_web_search_tool_produces_url_citations(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """Full flow (D7/Task 11): workspace PATCHed web_search_enabled=true, the
    tavily secret stored via PUT /api/v1/admin/secrets/tavily as superadmin,
    a FakeWebSearcher injected via create_app(web_searcher=...), and the
    completer scripting {"action":"web_search",...} on an escalating
    question. Asserts the full url-citation chain end to end: agent_step,
    sources frame, citations frame, the persisted Citation row, and the
    chat-history GET's CitationOut.url."""
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    fake_streamer = FakeStreamer()
    web_searcher = FakeWebSearcher()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=fake_streamer, chunk_reader=FakeChunkReader(),
        llm_completer=completer, web_searcher=web_searcher,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"web_search_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200 and r_ws.json()["web_search_enabled"] is True
        r_secret = await client.put(
            "/api/v1/admin/secrets/tavily", json={"value": "tvly-test-key"}, headers=h_super,
        )
        assert r_secret.status_code == 200
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={
                "content": "What is the muster point and when was it approved?",
                # RAGZ-PUB-08 item 2: external web search requires explicit consent.
                "web_search_consented": True,
            },
            headers=h_admin,
        )
        frames = parse_sse(r.text)
        names = [n for n, _ in frames]
        assert "agent_step" in names
        step = next(d for n, d in frames if n == "agent_step")
        assert step["tool"] == "web_search"
        # tool_result frame (design 2026-08-15, "Behind the scenes" UI):
        # same step index as agent_step, carrying the web_search hits
        # reshaped to {title, url, source} for the expandable result card.
        assert "tool_result" in names
        tool_result = next(d for n, d in frames if n == "tool_result")
        assert tool_result["n"] == step["n"]
        assert tool_result["tool"] == "web_search"
        assert tool_result["results"] == [
            {
                "title": web_searcher.results[0].title,
                "url": web_searcher.results[0].url,
                "source": "example.test",
                "snippet": web_searcher.results[0].snippet,
            },
        ]
        sources = next(d for n, d in frames if n == "sources")["sources"]
        web_source = sources[-1]
        assert web_source["url"] == web_searcher.results[0].url
        assert web_source["document_id"] == ""
        assert web_source["version"] == 0
        citations = next(d for n, d in frames if n == "citations")["citations"]
        assert citations[-1]["url"] == web_searcher.results[0].url
        done = next(d for n, d in frames if n == "done")

        row = (
            await session.execute(
                select(Citation).where(Citation.message_id == UUID(done["message_id"]))
            )
        ).scalar_one()
        assert row.url == web_searcher.results[0].url
        assert row.document_id is None

        r_hist = await client.get(f"/api/v1/chats/{chat_id}", headers=h_admin)
        assistant_node = next(
            m for m in r_hist.json()["messages"][0]["children"] if m["role"] == "assistant"
        )
        assert assistant_node["citations"][-1]["url"] == web_searcher.results[0].url

        # Cost reporting (design 2026-08-15): the default FakeWebSearcher is
        # NOT billable (mirrors the free DuckDuckGo default), so this metered
        # web_search flow records NO web_search usage row.
        from ragz.modules.quotas.models import UsageRecord

        web_rows = (
            await session.execute(
                select(UsageRecord).where(UsageRecord.feature == "web_search")
            )
        ).scalars().all()
        assert web_rows == []


async def test_web_search_records_usage_for_billable_provider(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """Cost reporting (design 2026-08-15 §2): a billable web-search provider
    (Tavily) records ONE feature='web_search' usage row (units=1, 0 tokens) per
    performed search, attributed to the asking user. Clones the citation flow
    above but injects a billable searcher and asserts the metered row.

    The explicit toggle (web_search_consented + web_search_enabled) forces the
    loop's FIRST step to be the web search, so the planner script is left empty
    -- exactly one web search runs, hence exactly one billable unit."""
    from ragz.modules.quotas.models import UsageRecord

    completer = FakeCompleter([])  # forced first web_search is the only search
    web_searcher = FakeWebSearcher(billable=True)
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer, web_searcher=web_searcher,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"web_search_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200
        r_secret = await client.put(
            "/api/v1/admin/secrets/tavily", json={"value": "tvly-test-key"}, headers=h_super,
        )
        assert r_secret.status_code == 200
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={
                "content": "What is the muster point and when was it approved?",
                "web_search_consented": True,
            },
            headers=h_admin,
        )
        frames = parse_sse(r.text)
        assert "agent_step" in [n for n, _ in frames]

        web_rows = (
            await session.execute(
                select(UsageRecord).where(
                    UsageRecord.org_id == seeded_user.org_id,
                    UsageRecord.feature == "web_search",
                )
            )
        ).scalars().all()
        assert len(web_rows) == 1
        assert web_rows[0].units == 1
        assert web_rows[0].prompt_tokens == 0 and web_rows[0].completion_tokens == 0
        assert web_rows[0].user_id == seeded_user.id


async def test_web_search_tool_result_frame_bounded_and_hostname_parsed(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """tool_result frame (design 2026-08-15, "Behind the scenes" UI): capped
    to 8 results and string fields bounded even when the searcher returns
    more/longer than that, and `source` is the parsed hostname with a
    leading www. stripped -- a hostile/misbehaving search provider can't
    inflate the SSE stream or the rendered card."""
    long_title = "x" * 500
    long_snippet = "y" * 800
    results = [
        WebResult(
            title=f"{long_title}-{i}", url=f"https://www.example{i}.test/path",
            snippet=long_snippet,
        )
        for i in range(12)
    ]
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    web_searcher = FakeWebSearcher(results=results)
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer, web_searcher=web_searcher,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"web_search_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200
        r_secret = await client.put(
            "/api/v1/admin/secrets/tavily", json={"value": "tvly-test-key"}, headers=h_super,
        )
        assert r_secret.status_code == 200
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={
                "content": "What is the muster point and when was it approved?",
                "web_search_consented": True,
            },
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    tool_result = next(d for n, d in frames if n == "tool_result")
    items = tool_result["results"]
    assert len(items) == 8  # capped, not the 12 the searcher returned
    assert all(len(item["title"]) <= 200 for item in items)
    assert all(len(item["url"]) <= 2048 for item in items)
    assert all(len(item["snippet"]) <= 300 for item in items)  # snippet bounded too
    assert items[0]["source"] == "example0.test"  # leading www. stripped


async def test_web_search_without_consent_never_calls_searcher_via_api(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """RAGZ-PUB-08 item 2 (API-level fail-closed): identical setup to
    test_web_search_tool_produces_url_citations -- web_search_enabled=True, the
    tavily secret stored, a searcher injected, the planner scripting a
    web_search action -- but the message body omits web_search_consented. The
    external searcher must NEVER be called; the consent gate defaults closed at
    the API boundary, not just in execute_tool's unit tests."""
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    web_searcher = FakeWebSearcher()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer, web_searcher=web_searcher,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"web_search_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200
        r_secret = await client.put(
            "/api/v1/admin/secrets/tavily", json={"value": "tvly-test-key"}, headers=h_super,
        )
        assert r_secret.status_code == 200
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            # no web_search_consented -> defaults False -> fail-closed
            json={"content": "What is the muster point and when was it approved?"},
            headers=h_admin,
        )
        assert r.status_code == 200
    # The external provider was never contacted despite full web-search setup.
    assert web_searcher.queries == []


async def test_consent_forces_loop_even_when_retrieval_grounds(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """Regression (web-search toggle ignored when docs matched): a user who
    explicitly toggles web search on for a turn must get an actual web search,
    even when local retrieval succeeds and the question would never escalate on
    its own. The question here is the SINGLE-part "What is the muster point?" --
    test_simple_question_never_escalates pins that it triggers zero planner
    calls -- and FakeRetriever grounds (no_answer=False). So neither the
    escalation heuristic nor the post-retrieval general-knowledge trigger can
    start the loop; only web_search_consented (the force_web gate in
    service.py) can. Asserts the loop ran, the searcher was actually called,
    and the web url reaches the citations -- not a docs-only answer. The
    planner is scripted to pick a LOCAL search (the exact behavior that made
    the toggle look ignored); the forced first step must still hit the web."""
    completer = FakeCompleter([_search_completion("nifty futures ltp expiry")])
    web_searcher = FakeWebSearcher()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),  # grounds: no_answer=False
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer, web_searcher=web_searcher,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"web_search_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200 and r_ws.json()["web_search_enabled"] is True
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={
                # single-part, non-escalating question + explicit consent
                "content": "What is the muster point?",
                "web_search_consented": True,
            },
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    # The loop ran BECAUSE of consent, not escalation:
    assert "agent_step" in names
    step = next(d for n, d in frames if n == "agent_step")
    assert step["tool"] == "web_search"
    # The external searcher was actually invoked (not a docs-only answer):
    assert web_searcher.queries != []
    # The web result reached grounding -- it appears among the turn's sources.
    # (Whether the synthesized answer cites [web] vs [doc] is down to the
    # FakeStreamer's fixed "[1]." marker; the grounding fact is the sources
    # frame, which is what "searched the web, not the docs" actually means.)
    sources = next(d for n, d in frames if n == "sources")["sources"]
    assert any(s.get("url") == web_searcher.results[0].url for s in sources)


async def test_web_search_not_offered_when_disabled_or_decline(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """Sub-cases against the planner prompt (FakeCompleter.calls):
    (a) toggle off -> planner system prompt contains no 'web_search';
    (b) toggle on but workspace decline -> no 'web_search';
    (c) toggle on, general_knowledge, NO tavily secret stored -> 'web_search'
        IS offered (DDG-01): the use_web gate in service.py no longer
        requires a Tavily secret, since the default provider
        (DuckDuckGoSearcher, wired in routes/chats.py) is keyless. This test
        injects a FakeWebSearcher directly (not the real DuckDuckGoSearcher),
        so (c) is really pinning "the gate itself doesn't block on a missing
        secret" -- who the real default provider is is covered by
        test_web.py's DuckDuckGoSearcher unit tests instead.

    One chat/app/model shared across all sub-cases (each PATCHes the
    workspace, then sends another message on the SAME chat -- a fresh
    make_model_and_chat per sub-case would collide on the unique
    litellm_model_name it always uses). The tavily secret is deliberately
    NEVER stored in this test."""
    completer = FakeCompleter(
        [_search_completion("muster point") for _ in range(3)]
    )
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer, web_searcher=FakeWebSearcher(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)

        async def _patch_and_ask(*, web_search_enabled: bool, fallback_policy: str) -> str:
            before = len(completer.calls)
            r_ws = await client.patch(
                f"/api/v1/workspaces/{chat_env['workspace'].id}",
                json={"web_search_enabled": web_search_enabled, "fallback_policy": fallback_policy},
                headers=h_admin,
            )
            assert r_ws.status_code == 200
            await client.post(
                f"/api/v1/chats/{chat_id}/messages",
                json={"content": "What is the muster point and when was it approved?"},
                headers=h_admin,
            )
            calls = completer.calls[before:]
            assert calls, "the planner should have been invoked"
            return calls[0]["messages"][0]["content"]

        # (a) toggle off (default general_knowledge policy)
        prompt_a = await _patch_and_ask(
            web_search_enabled=False, fallback_policy="general_knowledge"
        )
        assert "web_search" not in prompt_a

        # (b) toggle on but workspace decline
        prompt_b = await _patch_and_ask(web_search_enabled=True, fallback_policy="decline")
        assert "web_search" not in prompt_b

        # (c) toggle on, general_knowledge, NO tavily secret (never stored
        # above) -- DDG-01: still offered, since availability no longer
        # depends on a stored Tavily secret (the default provider is keyless).
        prompt_c = await _patch_and_ask(
            web_search_enabled=True, fallback_policy="general_knowledge"
        )
        assert "web_search" in prompt_c


# --- In-chat generative UI (design 2026-08-15): emission + persistence -----


async def test_generative_ui_disabled_by_default_no_blocks_frame(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """workspace.generative_ui_enabled defaults False: even with a completer
    present, the visualize step never runs -- no `blocks` SSE frame, no
    completer calls, message.blocks_json stays null. Same non-regression
    shape as test_simple_question_never_escalates above."""
    completer = FakeCompleter()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
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
    assert "blocks" not in names
    assert completer.calls == []
    done = next(d for n, d in frames if n == "done")
    msg = (
        await session.execute(select(Message).where(Message.id == UUID(done["message_id"])))
    ).scalar_one()
    assert msg.blocks_json is None


async def test_generative_ui_enabled_emits_and_persists_blocks(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """workspace.generative_ui_enabled=True + a completer scripted with a
    valid blocks JSON array: the `blocks` SSE frame is emitted AFTER
    citations (and before done), message.blocks_json is persisted, and
    history GET returns the same validated blocks."""
    completer = FakeCompleter([
        LLMCompletion(
            text='[{"type": "text", "markdown": "**Revenue grew 20%**"}]',
            tool_calls=[], usage=LLMUsage(prompt_tokens=8, completion_tokens=4),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"generative_ui_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200 and r_ws.json()["generative_ui_enabled"] is True
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is the muster point?"},
            headers=h_admin,
        )
        frames = parse_sse(r.text)
        names = [n for n, _ in frames]
        assert names.index("citations") < names.index("blocks") < names.index("done")
        blocks_frame = next(d for n, d in frames if n == "blocks")
        assert blocks_frame == {"blocks": [{"type": "text", "markdown": "**Revenue grew 20%**"}]}
        done = next(d for n, d in frames if n == "done")

        msg = (
            await session.execute(select(Message).where(Message.id == UUID(done["message_id"])))
        ).scalar_one()
        assert msg.blocks_json == [{"type": "text", "markdown": "**Revenue grew 20%**"}]

        r_hist = await client.get(f"/api/v1/chats/{chat_id}", headers=h_admin)
        assistant_node = next(
            m for m in r_hist.json()["messages"][0]["children"] if m["role"] == "assistant"
        )
        assert assistant_node["blocks"] == [{"type": "text", "markdown": "**Revenue grew 20%**"}]


async def test_generative_ui_hostile_completer_output_never_breaks_answer(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """generative_ui_enabled=True but the completer returns non-JSON,
    hostile-looking garbage: no crash, no `blocks` frame, and the answer/
    citations/done frames stream exactly as they would with the flag off --
    proving the visualize step is truly best-effort (Iron Rule 5)."""
    completer = FakeCompleter([
        LLMCompletion(
            text="<script>alert(1)</script> not json at all",
            tool_calls=[], usage=LLMUsage(prompt_tokens=8, completion_tokens=4),
        ),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
        llm_completer=completer,
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h_admin = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h_admin)
        r_ws = await client.patch(
            f"/api/v1/workspaces/{chat_env['workspace'].id}",
            json={"generative_ui_enabled": True}, headers=h_admin,
        )
        assert r_ws.status_code == 200
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": "What is the muster point?"},
            headers=h_admin,
        )
    assert r.status_code == 200
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "blocks" not in names
    assert names[-2:] == ["citations", "done"]
    answer = "".join(d["delta"] for n, d in frames if n == "token")
    assert answer == "Revenue was 12M [1]."
    done = next(d for n, d in frames if n == "done")
    msg = (
        await session.execute(select(Message).where(Message.id == UUID(done["message_id"])))
    ).scalar_one()
    assert msg.blocks_json is None


# --- Task 8: utility-model escalation tiebreak (design §1's deferred piece) -

# One interrogative ("Summarize" isn't one), no comparatives, no year/ISO
# date, no metadata-field match -> should_escalate alone returns False. Ten
# words -> is_ambiguous_for_escalation alone returns True. Only the
# combination (heuristic silent + ambiguous + a completer + a designated
# utility model) reaches the tiebreak.
_AMBIGUOUS_QUESTION = "Summarize the current evacuation procedure for the north building complex"


async def test_ambiguous_question_uses_utility_tiebreak_when_heuristic_silent(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession, utility_model: Model,
) -> None:
    """should_escalate alone would miss this long single-clause question (no
    second interrogative, no comparative, no date, no metadata match). With a
    utility model designated and a completer scripted to answer
    {"escalate": true} first, then the usual search->answer planner script,
    the tiebreak fires: the loop engages exactly as a heuristic-escalated
    question would, and the tiebreak's own usage is folded into the SAME
    summed total the agent loop and synthesize call feed."""
    completer = FakeCompleter([
        LLMCompletion(text='{"escalate": true}', tool_calls=[], usage=LLMUsage(15, 4)),
        _search_completion("evacuation procedure"),
    ])
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
        h_super = await auth(client, "root@platform.example")
        await _flag_tools_unreliable(client, h_super)
        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={"content": _AMBIGUOUS_QUESTION},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert names.index("retrieval_started") < names.index("agent_step") < names.index("sources")
    # The FIRST completer call is the tiebreak classifier, on the utility model.
    assert completer.calls[0]["model"] == utility_model.litellm_model_name
    step = next(d for n, d in frames if n == "agent_step")
    assert step == {"n": 1, "tool": "search", "query": "evacuation procedure"}
    done = next(d for n, d in frames if n == "done")
    # Usage summed: tiebreak (15/4) + synth (42/7 from FakeStreamer) +
    # planner rounds (10+3 / 5+1, same script-exhaustion shape as the
    # heuristic-escalation test above):
    assert done["prompt_tokens"] == 15 + 42 + 13
    assert done["completion_tokens"] == 4 + 7 + 6
    assert done["grounding"] == "documents"


async def test_ambiguous_question_without_utility_model_never_escalates(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession,
) -> None:
    """The SAME ambiguous question, no utility model designated anywhere:
    is_ambiguous_for_escalation alone can't reach the tiebreak (Plan I's
    original heuristics-only contract) -- zero completer calls, no
    agent_step frame, stream identical to the pre-Plan-J contract."""
    completer = FakeCompleter([_search_completion("evacuation procedure")])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
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
            json={"content": _AMBIGUOUS_QUESTION},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "agent_step" not in names
    assert completer.calls == []
    done = next(d for n, d in frames if n == "done")
    assert done["prompt_tokens"] == 42 and done["completion_tokens"] == 7
    assert done["grounding"] == "documents"


async def test_tiebreak_false_verdict_still_meters_usage_without_escalating(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, chat_env: dict[str, Any],
    seeded_user: Any, seeded_superadmin: Any, session: AsyncSession, utility_model: Model,
) -> None:
    """A utility model is designated and the tiebreak fires (heuristic
    silent + ambiguous), but the classifier answers {"escalate": false}: the
    turn must NOT escalate (no agent_step, single retrieval shot, direct
    synth), yet the classifier call still spent tokens and those tokens must
    still show up in the final summed usage -- the caller must meter a
    tiebreak call even on a False verdict."""
    completer = FakeCompleter([
        LLMCompletion(text='{"escalate": false}', tool_calls=[], usage=LLMUsage(12, 3)),
    ])
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id),
        llm_streamer=FakeStreamer(), chunk_reader=FakeChunkReader(),
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
            json={"content": _AMBIGUOUS_QUESTION},
            headers=h_admin,
        )
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "agent_step" not in names  # no escalation -- single retrieval shot
    assert len(completer.calls) == 1  # only the tiebreak classifier call
    assert completer.calls[0]["model"] == utility_model.litellm_model_name
    done = next(d for n, d in frames if n == "done")
    # Metered even on a False verdict: synth (42/7) + the spent-but-unused
    # tiebreak usage (12/3).
    assert done["prompt_tokens"] == 42 + 12 and done["completion_tokens"] == 7 + 3
    assert done["grounding"] == "documents"
