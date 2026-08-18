"""Agent loop unit tests (Phase 3 §2). This file grows across Tasks 7-9."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import UpstreamError
from ragz.modules.auth.models import User
from ragz.modules.chat.agent import (
    AgentGathered,
    AgentStep,
    AgentToolResult,
    PlannerAction,
    _daily_web_search_key,
    execute_tool,
    native_tool_specs,
    parse_planner_action,
    planner_system_prompt,
    run_agent_gather,
)
from ragz.modules.chat.llm import LLMCompletion, LLMToolCall, LLMUsage
from ragz.modules.documents.metadata import list_fields
from ragz.modules.models.models import Model
from ragz.modules.retrieval.client import COLLECTION
from ragz.modules.retrieval.service import RetrievedChunk
from ragz.modules.tenancy.context import TenantContext
from tests.conftest import FakeChunkReader, FakeCompleter, FakeRetriever, FakeWebSearcher

_ALL = ("search", "search_by_metadata", "get_document", "web_search", "answer")


@pytest.fixture
async def ctx(
    session: AsyncSession, seeded_user: User, chat_env: dict[str, Any]
) -> TenantContext:
    """TenantContext for chat_env's seeded workspace member. Also lazy-seeds
    the workspace's metadata field presets (documents.metadata.list_fields)
    so search_by_metadata tests hit a real, populated `department` field
    (and a genuinely absent `tenant_id`) via the real build_clauses."""
    ws = chat_env["workspace"]
    tenant_ctx = TenantContext(
        user_id=seeded_user.id, org_id=seeded_user.org_id, role=seeded_user.role,
        workspace_ids=frozenset({ws.id}),
    )
    await list_fields(session, tenant_ctx, ws.id)
    return tenant_ctx


def test_parse_bare_json() -> None:
    a = parse_planner_action('{"action": "search", "query": "muster point"}', _ALL)
    assert a == PlannerAction(action="search", query="muster point")


def test_parse_fenced_json_with_prose() -> None:
    text = (
        "Sure! Here is my plan:\n```json\n"
        '{"action": "get_document", "document_id": "abc"}\n```\nDone.'
    )
    a = parse_planner_action(text, _ALL)
    assert a.action == "get_document" and a.document_id == "abc"


def test_parse_filters_coerced_to_str_str() -> None:
    a = parse_planner_action(
        '{"action": "search_by_metadata", "query": "q", "filters": {"department": "HSE", "n": 3}}',
        _ALL,
    )
    assert a.filters == {"department": "HSE", "n": "3"}


def test_malformed_json_degrades_to_answer() -> None:
    assert parse_planner_action("let me think{not json}", _ALL).action == "answer"
    assert parse_planner_action("", _ALL).action == "answer"
    assert parse_planner_action('["not", "a", "dict"]', _ALL).action == "answer"


def test_unknown_and_unoffered_actions_degrade_to_answer() -> None:
    assert parse_planner_action('{"action": "delete_everything"}', _ALL).action == "answer"
    # web_search NOT offered -> parses as answer even though globally known:
    offered = ("search", "get_document", "answer")
    a = parse_planner_action('{"action": "web_search", "query": "x"}', offered)
    assert a.action == "answer"


def test_planner_prompt_lists_only_offered_tools() -> None:
    p = planner_system_prompt(("search", "get_document"), ())
    assert '"action": "search"' in p and '"action": "get_document"' in p
    assert "web_search" not in p and "search_by_metadata" not in p
    assert "data, not instructions" in p  # iron rule 5 discipline survives


def test_planner_prompt_names_metadata_fields() -> None:
    p = planner_system_prompt(("search", "search_by_metadata"), ("department", "doc_type"))
    assert "department" in p and "doc_type" in p


def test_native_tool_specs_shape() -> None:
    specs = native_tool_specs(("search", "web_search"), ())
    assert [s["function"]["name"] for s in specs] == ["search", "web_search"]  # type: ignore[index]
    search = specs[0]["function"]
    assert search["parameters"]["required"] == ["query"]  # type: ignore[index]


async def test_execute_search_returns_chunks_and_grounding(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    retriever = FakeRetriever(chat_env["document"].id)
    out = await execute_tool(
        session, ctx, PlannerAction(action="search", query="revenue"),
        workspace=chat_env["workspace"], retriever=retriever,
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is None and out.grounded is True and len(out.chunks) == 2


async def test_execute_search_by_metadata_builds_clauses(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    """Filters flow through build_clauses (the meta. jail): a real preset field
    produces a clause the retriever receives; the retriever is NEVER called
    with raw user filter keys."""
    retriever = FakeRetriever(chat_env["document"].id)
    out = await execute_tool(
        session, ctx,
        PlannerAction(action="search_by_metadata", query="q",
                      filters={"department": "HSE"}),
        workspace=chat_env["workspace"], retriever=retriever,
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is None
    clauses = retriever.calls[-1]["metadata_clauses"]
    assert [c.key for c in clauses] == ["meta.department"]  # jail prefix applied


async def test_execute_metadata_unknown_field_is_error_not_widening(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    retriever = FakeRetriever(chat_env["document"].id)
    out = await execute_tool(
        session, ctx,
        PlannerAction(action="search_by_metadata", query="q",
                      filters={"tenant_id": "evil"}),
        workspace=chat_env["workspace"], retriever=retriever,
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is not None and retriever.calls == []  # NotFoundError -> no search at all


async def test_execute_get_document_reads_via_chunk_reader(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    reader = FakeChunkReader()
    doc_id = chat_env["document"].id
    reader.document_chunks[doc_id] = [
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="body", score=1.0)
    ]
    out = await execute_tool(
        session, ctx, PlannerAction(action="get_document", document_id=str(doc_id)),
        workspace=chat_env["workspace"], retriever=FakeRetriever(doc_id),
        chunk_reader=reader, web_searcher=None, collection_name=COLLECTION,
    )
    assert out.grounded is True and out.chunks[0].text == "body"


async def test_execute_get_document_bad_uuid_is_error(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    out = await execute_tool(
        session, ctx, PlannerAction(action="get_document", document_id="not-a-uuid"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is not None and out.chunks == []


async def test_execute_web_search_disabled_is_error(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="news"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is not None


async def test_execute_metadata_malformed_date_is_error_not_exception(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    """build_clauses raises ConflictError (409) on an unparsable date value for
    a date-typed field (the preset revision_date). A wholly plausible
    planner/user input — must degrade to ToolOutcome.error, never raise past
    the seam (design §2's "tool failures are values" contract)."""
    retriever = FakeRetriever(chat_env["document"].id)
    out = await execute_tool(
        session, ctx,
        PlannerAction(action="search_by_metadata", query="q",
                      filters={"revision_date": "not-a-date"}),
        workspace=chat_env["workspace"], retriever=retriever,
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is not None and retriever.calls == []


async def test_execute_unknown_action_is_error(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    out = await execute_tool(
        session, ctx, PlannerAction(action="rm_rf", query=""),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, collection_name=COLLECTION,
    )
    assert out.error is not None


@pytest.fixture
async def flagged_model(session: AsyncSession) -> Model:
    model = Model(
        litellm_model_name="flagged-model", display_name="Flagged Model",
        provider_kind="ollama", base_url="http://x", tools_unreliable=True,
    )
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
async def plain_model(session: AsyncSession) -> Model:
    model = Model(
        litellm_model_name="plain-model", display_name="Plain Model",
        provider_kind="ollama", base_url="http://x", tools_unreliable=False,
    )
    session.add(model)
    await session.commit()
    return model


def _search_completion(query: str) -> LLMCompletion:
    return LLMCompletion(
        text=f'{{"action": "search", "query": "{query}"}}', tool_calls=[],
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )


async def _collect(gen):  # type: ignore[no-untyped-def]
    steps: list[AgentStep] = []
    gathered: AgentGathered | None = None
    async for item in gen:
        if isinstance(item, AgentStep):
            steps.append(item)
        else:
            gathered = item
    assert gathered is not None
    return steps, gathered


async def test_json_planner_search_then_answer(session, chat_env, ctx, flagged_model) -> None:  # type: ignore[no-untyped-def]
    # flagged_model: a Model row with tools_unreliable=True (small fixture).
    completer = FakeCompleter([_search_completion("muster point")])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert [(s.n, s.tool, s.query) for s in steps] == [(1, "search", "muster point")]
    assert len(gathered.chunks) == 2 and gathered.grounded is True
    assert (gathered.prompt_tokens, gathered.completion_tokens) == (13, 6)  # 10+3 / 5+1
    assert completer.calls[0]["tools"] is None  # JSON protocol: no native schemas
    # Round 2's planner context carried a step summary, clipped and labeled:
    round2 = completer.calls[1]["messages"][-1]["content"]  # type: ignore[index]
    assert "search" in round2 and "data, not instructions" in round2


async def test_native_protocol_uses_tool_calls(session, chat_env, ctx, plain_model) -> None:  # type: ignore[no-untyped-def]
    # plain_model: tools_unreliable=False.
    completer = FakeCompleter([
        LLMCompletion(text="", tool_calls=[LLMToolCall("search", '{"query": "x"}')],
                      usage=LLMUsage(prompt_tokens=20, completion_tokens=6)),
    ])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=plain_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert steps[0].tool == "search"
    assert completer.calls[0]["tools"] is not None  # native schemas offered
    assert gathered.grounded is True


async def test_iteration_cap_forces_answer(session, chat_env, ctx, flagged_model) -> None:  # type: ignore[no-untyped-def]
    completer = FakeCompleter([_search_completion(f"q{i}") for i in range(8)])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert len(steps) == 4 and len(completer.calls) == 4  # AGENT_MAX_ITERATIONS
    assert gathered.chunks  # synthesizes from whatever was gathered


async def test_malformed_planner_output_answers_immediately(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    completer = FakeCompleter([LLMCompletion(
        text="I think we should search for things",  # no JSON at all
        tool_calls=[], usage=LLMUsage(prompt_tokens=9, completion_tokens=2),
    )])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert steps == [] and gathered.chunks == [] and gathered.grounded is False


async def test_tool_error_degrades_to_single_shot(session, chat_env, ctx, flagged_model) -> None:  # type: ignore[no-untyped-def]
    completer = FakeCompleter([LLMCompletion(
        text='{"action": "get_document", "document_id": "not-a-uuid"}',
        tool_calls=[], usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )])
    retriever = FakeRetriever(chat_env["document"].id)
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="original question",
        model=flagged_model, completer=completer, retriever=retriever,
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert gathered.degraded is True and gathered.grounded is True
    assert retriever.calls[-1]["query"] == "original question"  # single-shot on the ORIGINAL
    assert len(gathered.chunks) == 2
    assert len(completer.calls) == 1  # loop stopped planning after the failure


class _RaisingRetriever:
    """Simulates the fallback retrieval hitting the SAME infra outage that
    triggered the degrade in the first place (e.g. embedding service/Qdrant
    down)."""

    async def __call__(  # type: ignore[no-untyped-def]
        self, session, ctx, workspace_id, query, top_k=None, metadata_clauses=None
    ):
        raise UpstreamError("embedding service unavailable")


async def test_fallback_retrieval_failure_does_not_crash(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """Review finding fix: the degrade-to-single-shot fallback call must be
    guarded the same way execute_tool guards every other retriever call. If
    the outage that caused the original tool failure also breaks the
    fallback retrieval, the loop must still degrade gracefully (never a dead
    end) instead of raising past run_agent_gather."""
    completer = FakeCompleter([LLMCompletion(
        text='{"action": "get_document", "document_id": "not-a-uuid"}',
        tool_calls=[], usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="original question",
        model=flagged_model, completer=completer, retriever=_RaisingRetriever(),
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert gathered.degraded is True
    assert gathered.grounded is False
    assert gathered.chunks == []
    assert len(completer.calls) == 1  # loop stopped planning after the failure


async def test_duplicate_chunks_deduped(session, chat_env, ctx, flagged_model) -> None:  # type: ignore[no-untyped-def]
    completer = FakeCompleter([_search_completion("a"), _search_completion("b")])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),  # same 2 chunks every call
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ))
    assert len(steps) == 2 and len(gathered.chunks) == 2  # not 4


def _web_search_completion(query: str) -> LLMCompletion:
    return LLMCompletion(
        text=f'{{"action": "web_search", "query": "{query}"}}', tool_calls=[],
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )


async def test_web_search_step_yields_tool_result_with_results(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """Design 2026-08-15 ("Behind the scenes" UI): a successful web_search
    step yields an AgentToolResult carrying the raw WebResults, tagged with
    the SAME step index as its preceding AgentStep, so stream_reply can pair
    the two into one tool_result SSE frame."""
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    web_searcher = FakeWebSearcher()
    steps: list[AgentStep] = []
    tool_results: list[AgentToolResult] = []
    gathered: AgentGathered | None = None
    async for item in run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, metadata_field_names=[],
        collection_name=COLLECTION, web_search_consented=True,
    ):
        if isinstance(item, AgentStep):
            steps.append(item)
        elif isinstance(item, AgentToolResult):
            tool_results.append(item)
        else:
            gathered = item
    assert gathered is not None
    assert [s.tool for s in steps] == ["web_search"]
    assert len(tool_results) == 1
    result = tool_results[0]
    assert result.n == steps[0].n and result.tool == "web_search"
    assert [r.url for r in result.web_results] == [web_searcher.results[0].url]


async def test_gathered_counts_performed_web_searches(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """Cost reporting (design 2026-08-15): AgentGathered.web_searches counts
    actually-performed web_search calls so stream_reply can meter them. A single
    consented, successful search -> web_searches == 1."""
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    gathered: AgentGathered | None = None
    async for item in run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=FakeWebSearcher(), metadata_field_names=[],
        collection_name=COLLECTION, web_search_consented=True,
    ):
        if isinstance(item, AgentGathered):
            gathered = item
    assert gathered is not None and gathered.web_searches == 1


async def test_force_web_first_overrides_planner_docs_choice(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """Regression (explicit toggle answered from docs): with force_web_first the
    turn is WEB-ONLY. Step 1 is a forced web_search on the ORIGINAL question,
    and even though the planner is scripted to pick a local `search` next, doc
    retrieval is not offered as a tool this turn -- so the scripted `search` is
    rejected and the loop answers instead of ever searching documents. No doc
    chunks pollute the answer when the user explicitly asked for the web."""
    completer = FakeCompleter([_search_completion("nifty futures ltp")])
    web_searcher = FakeWebSearcher()
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="Get latest nifty future value",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, metadata_field_names=[],
        collection_name=COLLECTION, web_search_consented=True, force_web_first=True,
    ))
    # Step 1 is the forced web search on the original question:
    assert steps[0].tool == "web_search"
    assert steps[0].query == "Get latest nifty future value"
    assert web_searcher.queries != []  # the web was actually searched
    # Web-only: no `search`/doc step appears even though the planner scripted one,
    # and no document chunks were gathered.
    assert [s.tool for s in steps] == ["web_search"]
    assert gathered.chunks == []


async def test_force_web_first_off_leaves_planner_in_control(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """Control for the regression above: same consented setup but
    force_web_first defaults off -> the planner's scripted local `search` is
    the first step (a plain consented turn never forces web-first)."""
    completer = FakeCompleter([_search_completion("nifty futures ltp")])
    steps, _ = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="Get latest nifty future value",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=FakeWebSearcher(), metadata_field_names=[],
        collection_name=COLLECTION, web_search_consented=True,  # no force_web_first
    ))
    assert steps[0].tool == "search"  # planner in control


async def test_non_web_search_step_never_yields_tool_result(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    completer = FakeCompleter([_search_completion("muster point")])
    tool_results: list[AgentToolResult] = []
    async for item in run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None, metadata_field_names=[],
        collection_name=COLLECTION,
    ):
        if isinstance(item, AgentToolResult):
            tool_results.append(item)
    assert tool_results == []


async def test_web_search_without_consent_never_yields_tool_result(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """No consent -> execute_tool refuses (ToolOutcome.error set) -> no
    web_results to show, so no AgentToolResult either."""
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    tool_results: list[AgentToolResult] = []
    async for item in run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=FakeWebSearcher(),
        metadata_field_names=[], collection_name=COLLECTION,
        web_search_consented=False,
    ):
        if isinstance(item, AgentToolResult):
            tool_results.append(item)
    assert tool_results == []


# --- RAGZ-PUB-08 residual: persistent per-user/day web-search cap ---------
# The per-turn budget above (web_search_budget/web_searches_used) resets on
# every run_agent_gather call -- these tests pin the SEPARATE, Redis-backed
# cap that does NOT reset between calls (i.e. survives across turns/
# messages/regenerations), enforced in execute_tool before the searcher.


async def test_daily_cap_blocks_before_searcher_when_already_at_limit(
    session, chat_env, ctx, redis_client
) -> None:
    """The whole point: pre-seed the Redis key AT the limit (simulating usage
    from earlier turns) and confirm a fresh execute_tool call refuses the
    search BEFORE the provider is ever called -- consent and the per-turn
    budget both pass, only the persistent cap blocks."""
    web_searcher = FakeWebSearcher()
    key = _daily_web_search_key(ctx.user_id)
    await redis_client.set(key, "2")
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="iso 45001"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, collection_name=COLLECTION,
        question="what is iso 45001?", web_search_consented=True,
        web_search_budget_remaining=3,
        redis=redis_client, web_search_daily_limit=2,
    )
    assert out.error is not None
    assert web_searcher.queries == []  # never reached the provider


async def test_daily_cap_persists_across_separate_calls_not_reset_per_turn(
    session, chat_env, ctx, redis_client
) -> None:
    """Two SEPARATE execute_tool calls (standing in for two separate chat
    turns/messages, each with its own fresh per-turn budget) sharing the same
    Redis key: the first is under the daily limit and proceeds; the second
    -- even though it is a brand-new call with a brand-new per-turn budget --
    is refused because the persistent counter carried over."""
    web_searcher = FakeWebSearcher()
    kwargs: dict[str, object] = dict(
        session=session, ctx=ctx, action=PlannerAction(action="web_search", query="q"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, collection_name=COLLECTION,
        question="q?", web_search_consented=True, web_search_budget_remaining=3,
        redis=redis_client, web_search_daily_limit=1,
    )
    turn1 = await execute_tool(**kwargs)  # type: ignore[arg-type]
    assert turn1.error is None
    assert len(web_searcher.queries) == 1

    turn2 = await execute_tool(**kwargs)  # type: ignore[arg-type]
    assert turn2.error is not None  # STAYS rejected on the next turn
    assert len(web_searcher.queries) == 1  # no second provider call


async def test_daily_cap_under_limit_search_proceeds_and_counter_increments(
    session, chat_env, ctx, redis_client
) -> None:
    web_searcher = FakeWebSearcher()
    key = _daily_web_search_key(ctx.user_id)
    assert await redis_client.get(key) is None
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="q"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, collection_name=COLLECTION,
        question="q?", web_search_consented=True, web_search_budget_remaining=3,
        redis=redis_client, web_search_daily_limit=5,
    )
    assert out.error is None
    assert len(web_searcher.queries) == 1
    assert int(await redis_client.get(key)) == 1


async def test_daily_cap_disabled_when_limit_zero_is_unlimited(
    session, chat_env, ctx, redis_client
) -> None:
    web_searcher = FakeWebSearcher()
    key = _daily_web_search_key(ctx.user_id)
    await redis_client.set(key, "999999")  # already way "over" any real cap
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="q"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, collection_name=COLLECTION,
        question="q?", web_search_consented=True, web_search_budget_remaining=3,
        redis=redis_client, web_search_daily_limit=0,
    )
    assert out.error is None
    assert len(web_searcher.queries) == 1


async def test_daily_cap_no_redis_means_no_persistent_cap(
    session, chat_env, ctx
) -> None:
    """Every EXISTING caller of execute_tool that doesn't pass `redis`
    (default None) must keep behaving exactly as before this fix -- no
    persistent cap enforced, even with a nonzero limit configured."""
    web_searcher = FakeWebSearcher()
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="q"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, collection_name=COLLECTION,
        question="q?", web_search_consented=True, web_search_budget_remaining=3,
        web_search_daily_limit=1,  # would block if redis were provided
    )
    assert out.error is None
    assert len(web_searcher.queries) == 1


async def test_run_agent_gather_daily_cap_blocks_web_search_end_to_end(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model, redis_client
) -> None:
    """Full threading check: run_agent_gather -> execute_tool with `redis`
    and `web_search_daily_limit` wired through refuses the search before the
    provider call and degrades to single-shot fallback (same failure posture
    as any other tool error), exactly like test_tool_error_degrades_to_
    single_shot above."""
    completer = FakeCompleter([_web_search_completion("iso 45001")])
    web_searcher = FakeWebSearcher()
    retriever = FakeRetriever(chat_env["document"].id)
    key = _daily_web_search_key(ctx.user_id)
    await redis_client.set(key, "1")
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer, retriever=retriever,
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, metadata_field_names=[],
        collection_name=COLLECTION, web_search_consented=True,
        redis=redis_client, web_search_daily_limit=1,
    ))
    assert web_searcher.queries == []  # refused before the provider call
    assert gathered.degraded is True  # tool error -> single-shot fallback
    assert retriever.calls[-1]["query"] == "q?"


async def test_daily_org_cap_blocks_even_when_user_cap_is_fine(
    session, chat_env, ctx, redis_client
) -> None:
    """Nice-to-have org-level cap: a fresh user (no per-user usage yet) is
    still refused once the shared per-org counter is at its own limit."""
    web_searcher = FakeWebSearcher()
    org_key = f"web_search_day:org:{ctx.org_id}:" + _daily_web_search_key(ctx.user_id).rsplit(
        ":", 1
    )[-1]
    await redis_client.set(org_key, "1")
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="q"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=web_searcher, collection_name=COLLECTION,
        question="q?", web_search_consented=True, web_search_budget_remaining=3,
        redis=redis_client, web_search_daily_limit=50, web_search_daily_org_limit=1,
    )
    assert out.error is not None
    assert web_searcher.queries == []


def _metadata_completion(query: str) -> LLMCompletion:
    return LLMCompletion(
        text=f'{{"action": "search_by_metadata", "query": "{query}", '
             '"filters": {"department": "HSE"}}',
        tool_calls=[], usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
    )


async def test_planner_rounds_do_not_hold_a_db_connection(  # type: ignore[no-untyped-def]
    session, chat_env, ctx, flagged_model
) -> None:
    """Phase 2 item 3: the loop must release its pooled connection across every
    planner round-trip. An AsyncSession holds a connection for as long as its
    transaction is open, and _plan is a full LLM call that takes no session at
    all -- so holding one there pins a connection per in-flight agent chat for
    the sum of its planner latencies and starves the pool for everyone else.

    Asserted on what the session's transaction state IS when the planner runs,
    not on some commit having been called. The action is search_by_metadata
    specifically because execute_tool routes it through the REAL
    build_clauses(session, ...) -- an actual Postgres read that opens a
    transaction. With FakeRetriever's plain `search` nothing touches the DB at
    all, so round 2 would read False whether or not the release exists and the
    test would pass against the unfixed code.
    """
    in_transaction: list[bool] = []

    class ProbingCompleter(FakeCompleter):
        async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            in_transaction.append(session.in_transaction())
            return await super().complete(**kwargs)

    completer = ProbingCompleter([_metadata_completion("muster point")])
    steps, gathered = await _collect(run_agent_gather(
        session, ctx, workspace=chat_env["workspace"], question="q?",
        model=flagged_model, completer=completer,
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None,
        metadata_field_names=["department"], collection_name=COLLECTION,
    ))
    # Two planner rounds: search_by_metadata, then the dry script's answer.
    # Round 2 is the load-bearing one -- build_clauses read from Postgres in
    # between, so without the release the session is still in that transaction.
    assert len(in_transaction) == 2
    assert in_transaction == [False, False]
    # The release must not cost the loop its results.
    assert [(s.n, s.tool) for s in steps] == [(1, "search_by_metadata")]
    assert gathered.grounded is True
