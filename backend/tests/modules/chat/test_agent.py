"""Agent loop unit tests (Phase 3 §2). This file grows across Tasks 7-9."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import UpstreamError
from raghub.modules.auth.models import User
from raghub.modules.chat.agent import (
    AgentGathered,
    AgentStep,
    PlannerAction,
    execute_tool,
    native_tool_specs,
    parse_planner_action,
    planner_system_prompt,
    run_agent_gather,
)
from raghub.modules.chat.llm import LLMCompletion, LLMToolCall, LLMUsage
from raghub.modules.documents.metadata import list_fields
from raghub.modules.models.models import Model
from raghub.modules.retrieval.client import COLLECTION
from raghub.modules.retrieval.service import RetrievedChunk
from raghub.modules.tenancy.context import TenantContext
from tests.conftest import FakeChunkReader, FakeCompleter, FakeRetriever

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
