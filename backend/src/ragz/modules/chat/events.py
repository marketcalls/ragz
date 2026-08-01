"""Typed SSE events for chat streaming (spec 3.4).

SSEEvent.encode() is the single serialization point - no route or service
builds `data:` strings by hand. The payload shapes here are the wire contract
consumed by the frontend (Plan D).
"""

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: dict[str, object]

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, separators=(',', ':'))}\n\n"


@dataclass(frozen=True)
class SourceRef:
    marker: int
    document_id: str
    filename: str
    page: int
    chunk_index: int
    score: float
    snippet: str
    section: str | None
    version: int
    # Phase 3 Plan I Task 11 (D7): set for web-search hits only; wire strings
    # (document_id etc.) stay non-null — a web source uses document_id="".
    url: str | None


@dataclass(frozen=True)
class CitationRef:
    marker: int
    document_id: str
    chunk_ref: str
    page: int
    score: float
    section: str | None
    version: int
    url: str | None


def retrieval_started_event() -> SSEEvent:
    return SSEEvent("retrieval_started", {})


def sources_event(sources: list[SourceRef]) -> SSEEvent:
    return SSEEvent("sources", {"sources": [asdict(s) for s in sources]})


def token_event(delta: str) -> SSEEvent:
    return SSEEvent("token", {"delta": delta})


def citations_event(citations: list[CitationRef]) -> SSEEvent:
    return SSEEvent("citations", {"citations": [asdict(c) for c in citations]})


def done_event(
    *, message_id: str, prompt_tokens: int, completion_tokens: int, no_answer: bool,
    grounding: str, validation_failed: bool = False,
) -> SSEEvent:
    return SSEEvent(
        "done",
        {
            "message_id": message_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "no_answer": no_answer,
            "grounding": grounding,
            "validation_failed": validation_failed,
        },
    )


def error_event(detail: str) -> SSEEvent:
    return SSEEvent("error", {"detail": detail})


def agent_step_event(*, n: int, tool: str, query: str) -> SSEEvent:
    """Phase 3 (design §2): emitted before each agent tool execution.
    Additive — pre-Plan-I clients never see it (they receive it only on
    escalated turns, and the frontend ships the handler in the same commit)."""
    return SSEEvent("agent_step", {"n": n, "tool": tool, "query": query})
