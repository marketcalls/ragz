"""Agent loop unit tests (Phase 3 §2). This file grows across Tasks 7-9."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.chat.agent import (
    PlannerAction,
    execute_tool,
    native_tool_specs,
    parse_planner_action,
    planner_system_prompt,
)
from raghub.modules.documents.metadata import list_fields
from raghub.modules.retrieval.service import RetrievedChunk
from raghub.modules.tenancy.context import TenantContext
from tests.conftest import FakeChunkReader, FakeRetriever

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
        chunk_reader=FakeChunkReader(), web_searcher=None,
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
        chunk_reader=FakeChunkReader(), web_searcher=None,
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
        chunk_reader=FakeChunkReader(), web_searcher=None,
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
        chunk_reader=reader, web_searcher=None,
    )
    assert out.grounded is True and out.chunks[0].text == "body"


async def test_execute_get_document_bad_uuid_is_error(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    out = await execute_tool(
        session, ctx, PlannerAction(action="get_document", document_id="not-a-uuid"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None,
    )
    assert out.error is not None and out.chunks == []


async def test_execute_web_search_disabled_is_error(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    out = await execute_tool(
        session, ctx, PlannerAction(action="web_search", query="news"),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None,
    )
    assert out.error is not None


async def test_execute_unknown_action_is_error(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    out = await execute_tool(
        session, ctx, PlannerAction(action="rm_rf", query=""),
        workspace=chat_env["workspace"], retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(), web_searcher=None,
    )
    assert out.error is not None
