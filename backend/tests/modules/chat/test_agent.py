"""Agent loop unit tests (Phase 3 §2). This file grows across Tasks 7-9."""

from raghub.modules.chat.agent import (
    PlannerAction,
    native_tool_specs,
    parse_planner_action,
    planner_system_prompt,
)

_ALL = ("search", "search_by_metadata", "get_document", "web_search", "answer")


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
