import json
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import set_app_setting
from ragz.core.config import Settings
from ragz.core.errors import UpstreamError
from ragz.modules.auth.models import User
from ragz.modules.chat import web
from ragz.modules.chat.agent import (
    AgentGathered,
    PlannerAction,
    execute_tool,
    run_agent_gather,
)
from ragz.modules.chat.llm import LLMCompletion, LLMUsage
from ragz.modules.models.models import Model
from ragz.modules.secrets import service as secrets_service
from ragz.modules.tenancy.context import TenantContext
from tests.conftest import FakeChunkReader, FakeCompleter, FakeRetriever, FakeWebSearcher

_KEY = "pplx-test-key-never-in-output"
_ANSWER = "Public guidance recommends regular reviews [1]."


def _response() -> dict[str, object]:
    return {
        "id": "response-test",
        "status": "completed",
        "output": [
            {
                "type": "search_results",
                "results": [
                    {"id": 1, "title": "Official guidance", "url": "https://example.org/guide"},
                    {"id": 2, "title": "Duplicate", "url": "https://example.org/guide"},
                    {"id": 3, "title": "Unsafe", "url": "javascript:alert(1)"},
                ],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": _ANSWER,
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.org/policy",
                                "title": "Policy",
                                "start_index": 0,
                                "end_index": 15,
                            },
                        ],
                    },
                ],
            },
        ],
        "usage": {"input_tokens": 30, "output_tokens": 20},
    }


async def _researcher(session: AsyncSession, settings: Settings, handler: Any) -> Any:
    assert hasattr(web, "PerplexityResearcher"), "Perplexity research is not implemented"
    await secrets_service.set_secret(
        session,
        actor_id=None,
        name="perplexity",
        value=_KEY,
        settings=settings,
    )
    return web.PerplexityResearcher(settings=settings, transport=httpx.MockTransport(handler))


async def test_perplexity_request_and_citations_keep_synthesis_separate(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.perplexity.ai/v1/agent"
        assert request.headers["Authorization"] == f"Bearer {_KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-5.6-luna"
        assert payload["input"] == "public guidance"
        assert payload["tools"] == [
            {"type": "web_search", "search_context_size": "medium", "max_results": 10}
        ]
        assert payload["max_output_tokens"] == 1500
        return httpx.Response(200, json=_response())

    researcher = await _researcher(session, test_settings, handler)
    result = await researcher(session, "public guidance")
    assert result.answer == _ANSWER
    assert [c.url for c in result.citations] == [
        "https://example.org/guide",
        "https://example.org/policy",
    ]
    shaped = result.as_web_results()
    assert shaped[0].result_kind == "answer"
    assert "third-party synthesis" in shaped[0].snippet
    assert _ANSWER in shaped[0].snippet
    assert "https://example.org/guide" in shaped[0].snippet
    assert all(_ANSWER not in source.snippet for source in shaped[1:])
    assert _KEY not in repr(result)


@pytest.mark.parametrize("body", [[], {}, {"output": "bad"}, {"output": [{"type": "message"}]}])
async def test_perplexity_malformed_response_is_a_safe_upstream_error(
    session: AsyncSession,
    test_settings: Settings,
    body: Any,
) -> None:
    researcher = await _researcher(
        session,
        test_settings,
        lambda request: httpx.Response(200, json=body),
    )
    with pytest.raises(UpstreamError, match="malformed|no answer"):
        await researcher(session, "public guidance")


async def test_perplexity_error_does_not_echo_provider_body_or_key(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    researcher = await _researcher(
        session,
        test_settings,
        lambda request: httpx.Response(401, text=f"diagnostic {_KEY} sensitive query"),
    )
    with pytest.raises(UpstreamError) as caught:
        await researcher(session, "public guidance")
    assert _KEY not in str(caught.value)
    assert "sensitive query" not in str(caught.value)
    assert "401" in str(caught.value)


async def test_perplexity_selection_keeps_link_search_and_requires_its_key(
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    assert hasattr(web, "build_web_researcher"), "Research selection is not implemented"
    await set_app_setting(session, "web_search_provider", "perplexity")
    assert isinstance(await web.build_web_searcher(session, test_settings), web.DuckDuckGoSearcher)
    assert await web.build_web_researcher(session, test_settings) is None
    await secrets_service.set_secret(
        session,
        actor_id=None,
        name="perplexity",
        value=_KEY,
        settings=test_settings,
    )
    assert isinstance(
        await web.build_web_researcher(session, test_settings), web.PerplexityResearcher
    )
    await set_app_setting(session, "web_search_provider", "duckduckgo")
    assert await web.build_web_researcher(session, test_settings) is None


@pytest.mark.parametrize("consented,budget,error", [(False, 2, "consent"), (True, 0, "budget")])
async def test_research_refuses_without_shared_consent_and_budget(
    session: AsyncSession,
    test_settings: Settings,
    seeded_user: User,
    chat_env: dict[str, Any],
    consented: bool,
    budget: int,
    error: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Refused research must not call the provider")

    researcher = await _researcher(session, test_settings, handler)
    ctx = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        role="admin",
        workspace_ids=frozenset({chat_env["workspace"].id}),
    )
    outcome = await execute_tool(
        session,
        ctx,
        PlannerAction(action="web_research", query="guidance"),
        workspace=chat_env["workspace"],
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(),
        web_searcher=FakeWebSearcher(),
        web_researcher=researcher,
        collection_name="unused",
        question="public guidance",
        web_search_consented=consented,
        web_search_budget_remaining=budget,
    )
    assert error in (outcome.error or "")


async def test_research_shares_daily_cap_with_links_and_redacts_query(
    session: AsyncSession,
    test_settings: Settings,
    seeded_user: User,
    chat_env: dict[str, Any],
    redis_client: Redis,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"] == "public guidance"
        return httpx.Response(200, json=_response())

    researcher = await _researcher(session, test_settings, handler)
    ctx = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        role="admin",
        workspace_ids=frozenset({chat_env["workspace"].id}),
    )
    kwargs = dict(
        workspace=chat_env["workspace"],
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(),
        web_searcher=FakeWebSearcher(),
        web_researcher=researcher,
        collection_name="unused",
        question="public guidance alice@example.org api_key=supersecret",
        web_search_consented=True,
        web_search_budget_remaining=2,
        redis=redis_client,
        web_search_daily_limit=1,
    )
    result = await execute_tool(
        session,
        ctx,
        PlannerAction(action="web_research", query="public guidance POISON"),
        **kwargs,
    )
    assert result.error is None
    refused = await execute_tool(
        session,
        ctx,
        PlannerAction(action="web_search", query="public guidance"),
        **kwargs,
    )
    assert refused.error == "daily web search limit reached"


async def test_successful_paid_research_records_usage_at_provider_boundary(
    session: AsyncSession,
    test_settings: Settings,
    seeded_user: User,
    chat_env: dict[str, Any],
) -> None:
    researcher = await _researcher(
        session,
        test_settings,
        lambda request: httpx.Response(200, json=_response()),
    )
    ctx = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        role="admin",
        workspace_ids=frozenset({chat_env["workspace"].id}),
    )
    recorded = 0

    async def record_usage() -> None:
        nonlocal recorded
        recorded += 1

    outcome = await execute_tool(
        session,
        ctx,
        PlannerAction(action="web_research", query="public guidance"),
        workspace=chat_env["workspace"],
        retriever=FakeRetriever(chat_env["document"].id),
        chunk_reader=FakeChunkReader(),
        web_searcher=FakeWebSearcher(),
        web_researcher=researcher,
        collection_name="unused",
        question="public guidance",
        web_search_consented=True,
        web_search_budget_remaining=1,
        record_billable_web_usage=record_usage,
    )

    assert outcome.error is None
    assert recorded == 1


async def test_loop_combines_link_and_research_budget_and_paid_usage(
    session: AsyncSession,
    test_settings: Settings,
    seeded_user: User,
    chat_env: dict[str, Any],
) -> None:
    researcher = await _researcher(
        session,
        test_settings,
        lambda request: httpx.Response(200, json=_response()),
    )
    ctx = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        role="admin",
        workspace_ids=frozenset({chat_env["workspace"].id}),
    )
    model = Model(litellm_model_name="planner", provider_kind="ollama", tools_unreliable=True)
    completer = FakeCompleter(
        [
            LLMCompletion(
                text=json.dumps({"action": name, "query": "guidance"}),
                tool_calls=[],
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            )
            for name in ["web_research", "web_search", "web_research"]
        ]
    )
    items = [
        item
        async for item in run_agent_gather(
            session,
            ctx,
            workspace=chat_env["workspace"],
            question="public guidance",
            model=model,
            completer=completer,
            retriever=FakeRetriever(chat_env["document"].id),
            chunk_reader=FakeChunkReader(),
            web_searcher=FakeWebSearcher(),
            web_researcher=researcher,
            metadata_field_names=[],
            collection_name="unused",
            web_search_consented=True,
            web_search_budget=2,
        )
    ]
    gathered = items[-1]
    assert isinstance(gathered, AgentGathered)
    assert gathered.web_searches == 2
    assert gathered.billable_web_searches == 1
    assert gathered.degraded


@pytest.mark.parametrize("key", ["pplx-abc123456789012345", "tvly-dev-abc123456789012345"])
def test_redaction_catches_search_provider_keys(key: str) -> None:
    assert key not in web.redact_query(f"guidance {key}")


async def test_research_sources_render_as_synthesis_without_impersonating_cited_pages(
    session: AsyncSession,
    test_settings: Settings,
    seeded_user: User,
) -> None:
    from ragz.modules.chat.prompting import render_data_blocks
    from ragz.modules.chat.service import _prepare_sources

    researcher = await _researcher(
        session,
        test_settings,
        lambda request: httpx.Response(200, json=_response()),
    )
    research = await researcher(session, "public guidance")
    ctx = TenantContext(
        user_id=seeded_user.id, org_id=seeded_user.org_id, role="admin", workspace_ids=frozenset()
    )
    refs, sources = await _prepare_sources(
        session,
        ctx,
        [],
        max_tokens=4000,
        model_hint=None,
        web_results=research.as_web_results(),
    )
    rendered = render_data_blocks(sources)
    assert 'kind="answer"' in rendered
    assert 'kind="answer_citation"' in rendered
    assert "third-party synthesis" in rendered
    assert refs[1].url == "https://example.org/guide"
    assert _ANSWER not in sources[1].text


@pytest.mark.parametrize("second_tool", ["web_search", "web_research"])
async def test_later_sources_preserve_independent_research_and_primary_text(
    session: AsyncSession,
    test_settings: Settings,
    seeded_user: User,
    chat_env: dict[str, Any],
    second_tool: str,
) -> None:
    answers = iter(["First synthesis.", "Second independent synthesis."])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "search_results",
                        "results": [
                            {"id": 1, "title": "Guidance", "url": "https://example.org/guide"},
                        ],
                    },
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": next(answers)}],
                    },
                ]
            },
        )

    researcher = await _researcher(session, test_settings, handler)
    ctx = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        role="admin",
        workspace_ids=frozenset({chat_env["workspace"].id}),
    )
    completer = FakeCompleter(
        [
            LLMCompletion(
                text=json.dumps({"action": action, "query": "guidance"}),
                tool_calls=[],
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            )
            for action in ["web_research", second_tool]
        ]
    )
    items = [
        item
        async for item in run_agent_gather(
            session,
            ctx,
            workspace=chat_env["workspace"],
            question="public guidance",
            model=Model(
                litellm_model_name="planner", provider_kind="ollama", tools_unreliable=True
            ),
            completer=completer,
            retriever=FakeRetriever(chat_env["document"].id),
            chunk_reader=FakeChunkReader(),
            web_researcher=researcher,
            web_searcher=FakeWebSearcher(
                [
                    web.WebResult(
                        title="Official guidance",
                        url="https://example.org/guide",
                        snippet="Actual page text.",
                    )
                ]
            ),
            metadata_field_names=[],
            collection_name="unused",
            web_search_consented=True,
        )
    ]
    gathered = items[-1]
    assert isinstance(gathered, AgentGathered)
    if second_tool == "web_search":
        source = next(r for r in gathered.web_results if r.url == "https://example.org/guide")
        assert source.snippet == "Actual page text."
        assert source.result_kind == "links"
    else:
        answers_gathered = [r for r in gathered.web_results if r.result_kind == "answer"]
        assert len(answers_gathered) == 2
        assert "Second independent synthesis." in answers_gathered[-1].snippet
