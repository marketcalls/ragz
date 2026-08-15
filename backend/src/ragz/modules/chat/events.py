"""Typed SSE events for chat streaming (spec 3.4).

SSEEvent.encode() is the single serialization point - no route or service
builds `data:` strings by hand. The payload shapes here are the wire contract
consumed by the frontend (Plan D).
"""

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ragz.modules.chat.blocks import Block


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


@dataclass(frozen=True)
class ToolResultItem:
    """One `tool_result` entry (display-only): the same web-search hits that
    already became citations, reshaped for the "Behind the scenes" UI's
    expandable web_search card. `source` is the result's hostname (leading
    `www.` stripped) so the card can right-align it next to the title
    without the frontend fetching or parsing anything itself."""

    title: str
    url: str
    source: str


def tool_result_event(*, n: int, tool: str, results: list[ToolResultItem]) -> SSEEvent:
    """"Behind the scenes" collapsible (design 2026-08-15): emitted right
    after a `web_search` agent step whose results are already known,
    carrying the same step index `n` as the preceding agent_step frame so
    the frontend can pair them. Display-only — these results already reached
    the client as `sources`/`citations`; this frame just surfaces them
    grouped under their originating tool call. Additive, like agent_step: a
    client that ignores unknown SSE event names sees no behavior change."""
    return SSEEvent("tool_result", {"n": n, "tool": tool, "results": [asdict(r) for r in results]})


def blocks_event(blocks: "list[Block]") -> SSEEvent:
    """In-chat generative UI (design 2026-08-15, §2): emitted AFTER citations,
    only when workspace.generative_ui_enabled + a completer are both present
    AND the visualize step (blocks_emit.generate_blocks) actually returned at
    least one validated block. Additive — a client that ignores unknown SSE
    event names sees no behavior change when this frame is absent (the
    default, flag-off case)."""
    return SSEEvent("blocks", {"blocks": [b.model_dump(mode="json") for b in blocks]})
