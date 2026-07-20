import json

import httpx
import pytest

from raghub.core.errors import UpstreamError
from raghub.modules.chat.llm import LiteLLMStreamer, LLMCompletion, LLMDelta, LLMUsage


def sse_body(chunks: list[dict[str, object]]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def delta_chunk(text: str) -> dict[str, object]:
    return {"choices": [{"delta": {"content": text}}]}


async def collect(streamer: LiteLLMStreamer) -> list[LLMDelta | LLMUsage]:
    return [
        item
        async for item in streamer.stream(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
        )
    ]


def make(transport: httpx.MockTransport) -> LiteLLMStreamer:
    return LiteLLMStreamer(
        base_url="http://litellm.test", master_key="sk-test", transport=transport
    )


async def test_streams_deltas_then_usage() -> None:
    body = sse_body([
        delta_chunk("Hel"), delta_chunk("lo"),
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}},
    ])
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})

    items = await collect(make(httpx.MockTransport(handler)))
    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert items == [LLMDelta("Hel"), LLMDelta("lo"), LLMUsage(12, 2)]


async def test_non_200_maps_to_upstream_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": "bad key"})
    )
    with pytest.raises(UpstreamError):
        await collect(make(transport))


async def test_connect_error_maps_to_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(UpstreamError):
        await collect(make(httpx.MockTransport(handler)))


async def test_malformed_json_in_stream_maps_to_upstream_error() -> None:
    body = b"data: {not-json\n\n"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=body,
                                      headers={"content-type": "text/event-stream"})
    )
    with pytest.raises(UpstreamError, match="malformed stream chunk from gateway"):
        await collect(make(transport))


def _completion_handler(body: dict) -> httpx.Response:  # type: ignore[no-untyped-def]
    return httpx.Response(200, json=body)


async def test_complete_parses_text_and_usage() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _completion_handler({
            "choices": [{"message": {"content": '{"action": "answer"}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
        })

    s = LiteLLMStreamer(base_url="http://llm", master_key="k",
                        transport=httpx.MockTransport(handler))
    out: LLMCompletion = await s.complete(model="m", messages=[{"role": "user", "content": "q"}])
    assert out.text == '{"action": "answer"}'
    assert out.tool_calls == [] and out.usage.prompt_tokens == 11
    assert seen[0]["stream"] is False and "tools" not in seen[0]


async def test_complete_passes_tools_and_parses_tool_calls() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _completion_handler({
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "search", "arguments": '{"query": "muster"}'}},
            ]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6},
        })

    s = LiteLLMStreamer(base_url="http://llm", master_key="k",
                        transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "search", "parameters": {}}}]
    out = await s.complete(model="m", messages=[{"role": "user", "content": "q"}], tools=tools)
    assert seen[0]["tools"] == tools
    assert out.text == ""  # null content normalizes to empty string
    assert out.tool_calls[0].name == "search"
    assert out.tool_calls[0].arguments == '{"query": "muster"}'


async def test_complete_non_200_raises_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    s = LiteLLMStreamer(base_url="http://llm", master_key="k",
                        transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError):
        await s.complete(model="m", messages=[])


async def test_complete_network_error_raises_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    s = LiteLLMStreamer(base_url="http://llm", master_key="k",
                        transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError):
        await s.complete(model="m", messages=[])


async def test_stream_includes_reasoning_effort_when_set() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, content=sse_body([delta_chunk("hi")]),
            headers={"content-type": "text/event-stream"},
        )

    streamer = make(httpx.MockTransport(handler))
    async for _ in streamer.stream(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="high",
    ):
        pass
    assert seen[0]["reasoning_effort"] == "high"


async def test_stream_omits_reasoning_effort_when_none() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, content=sse_body([delta_chunk("hi")]),
            headers={"content-type": "text/event-stream"},
        )

    async for _ in make(httpx.MockTransport(handler)).stream(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
    ):
        pass
    assert "reasoning_effort" not in seen[0]


async def test_complete_includes_reasoning_effort_when_set() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _completion_handler({"choices": [{"message": {"content": "ok"}}]})

    streamer = make(httpx.MockTransport(handler))
    await streamer.complete(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="low",
    )
    assert seen[0]["reasoning_effort"] == "low"


async def test_complete_omits_reasoning_effort_when_off() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _completion_handler({"choices": [{"message": {"content": "ok"}}]})

    streamer = make(httpx.MockTransport(handler))
    await streamer.complete(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="off",
    )
    assert "reasoning_effort" not in seen[0]


async def test_complete_satisfies_plan_k_utility_completion_contract() -> None:
    """Plan K Task 1 contract pin: enrichment (Task 3) and rolling-summary
    memory (Task 8) call `LiteLLMStreamer.complete(model=..., messages=...)`
    exactly like this — no `tools` kwarg — and only read `result.text` and
    `result.usage.{prompt,completion}_tokens`. Plan J's `LLMCompletion` is a
    superset (it also carries `tool_calls`) but satisfies this minimal
    two-field contract without any new primitive being defined."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is False
        assert "tools" not in body
        return _completion_handler({
            "choices": [{"message": {"content": '{"summary": "ok"}'}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3},
        })

    s = LiteLLMStreamer(base_url="http://llm", master_key="k",
                        transport=httpx.MockTransport(handler))
    result: LLMCompletion = await s.complete(
        model="utility-model", messages=[{"role": "user", "content": "summarize"}]
    )
    assert isinstance(result, LLMCompletion)
    assert result.text == '{"summary": "ok"}'
    assert result.usage.prompt_tokens == 9 and result.usage.completion_tokens == 3
