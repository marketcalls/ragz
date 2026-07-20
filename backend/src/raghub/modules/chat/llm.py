"""Thin streaming client for the LiteLLM gateway (OpenAI-compatible SSE).

The LLMStreamer Protocol is the unit-test seam: chat streaming tests inject a
fake streamer; only this module talks HTTP (mocked at the httpx layer — the
one sanctioned mock).
"""

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol

import httpx

from raghub.core.errors import UpstreamError


@dataclass(frozen=True)
class LLMDelta:
    text: str


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class LLMToolCall:
    name: str
    arguments: str  # provider's raw JSON string — parsed leniently by the loop


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    tool_calls: list[LLMToolCall]
    usage: LLMUsage


class LLMStreamer(Protocol):
    def stream(
        self, *, model: str, messages: list[dict[str, str]],
        reasoning_effort: str | None = None,
    ) -> AsyncGenerator[LLMDelta | LLMUsage, None]: ...


class LLMCompleter(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, object]] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMCompletion: ...


class LiteLLMStreamer:
    def __init__(
        self,
        *,
        base_url: str,
        master_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        limits: httpx.Limits | None = None,
    ) -> None:
        self._base_url = base_url
        self._master_key = master_key
        self._transport = transport
        # httpx's own built-in default (100 connections / 20 keepalive) matches the
        # settings defaults below - passing None here preserves that behavior.
        self._limits = limits if limits is not None else httpx.Limits()

    async def stream(
        self, *, model: str, messages: list[dict[str, str]],
        reasoning_effort: str | None = None,
    ) -> AsyncGenerator[LLMDelta | LLMUsage, None]:
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if reasoning_effort is not None and reasoning_effort != "off":
            payload["reasoning_effort"] = reasoning_effort
        headers = {"Authorization": f"Bearer {self._master_key}"}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, transport=self._transport,
                timeout=httpx.Timeout(120.0, connect=10.0), limits=self._limits,
            ) as client:
                async with client.stream(
                    "POST", "/v1/chat/completions", json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        body_str = body.decode(errors="replace")[:200]
                        msg = f"LLM gateway returned {response.status_code}: {body_str}"
                        raise UpstreamError(msg)
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line.removeprefix("data: ").strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except (json.JSONDecodeError, ValueError) as exc:
                            raise UpstreamError("malformed stream chunk from gateway") from exc
                        usage = chunk.get("usage")
                        if usage:
                            yield LLMUsage(
                                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                                completion_tokens=int(usage.get("completion_tokens", 0)),
                            )
                            continue
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta", {}).get("content")
                            if delta:
                                yield LLMDelta(text=delta)
        except httpx.HTTPError as exc:
            raise UpstreamError("LLM gateway unreachable") from exc

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, object]] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMCompletion:
        """One non-streaming completion (agent planner rounds). Optional
        OpenAI-style `tools` for models that do native tool calling; the raw
        tool_calls come back untouched — the agent loop owns lenient parsing."""
        payload: dict[str, object] = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        if reasoning_effort is not None and reasoning_effort != "off":
            payload["reasoning_effort"] = reasoning_effort
        headers = {"Authorization": f"Bearer {self._master_key}"}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, transport=self._transport,
                timeout=httpx.Timeout(120.0, connect=10.0), limits=self._limits,
            ) as client:
                response = await client.post(
                    "/v1/chat/completions", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            raise UpstreamError("LLM gateway unreachable") from exc
        if response.status_code != 200:
            body_str = response.text[:200]
            raise UpstreamError(f"LLM gateway returned {response.status_code}: {body_str}")
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError("malformed completion from gateway") from exc
        choices = body.get("choices") or []
        message: dict[str, object] = choices[0].get("message") or {} if choices else {}
        raw_tool_calls = message.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            raw_tool_calls = []
        tool_calls = [
            LLMToolCall(
                name=str((tc.get("function") or {}).get("name", "")),
                arguments=str((tc.get("function") or {}).get("arguments") or "{}"),
            )
            for tc in raw_tool_calls
            if isinstance(tc, dict)
        ]
        usage_raw = body.get("usage") or {}
        return LLMCompletion(
            text=str(message.get("content") or ""),
            tool_calls=tool_calls,
            usage=LLMUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
                completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            ),
        )
