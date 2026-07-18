import json

import httpx
import pytest

from raghub.core.errors import UpstreamError
from raghub.modules.chat.llm import LiteLLMStreamer, LLMDelta, LLMUsage


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
