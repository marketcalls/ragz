import json
from dataclasses import asdict

from raghub.modules.chat.events import (
    CitationRef,
    SourceRef,
    agent_step_event,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    sources_event,
    token_event,
)


def test_encode_frame_format() -> None:
    frame = token_event("Hel\nlo").encode()
    assert frame.startswith("event: token\ndata: ")
    assert frame.endswith("\n\n")
    payload = frame.split("data: ", 1)[1].rstrip("\n")
    assert json.loads(payload) == {"delta": "Hel\nlo"}  # newline stays inside JSON


def test_all_event_names_and_payloads() -> None:
    src = SourceRef(marker=1, document_id="d-1", filename="a.pdf", page=2,
                    chunk_index=0, score=0.9, snippet="text",
                    section="Intro > Overview", version=2, url=None)
    cit = CitationRef(marker=1, document_id="d-1", chunk_ref="d-1:2:0", page=2, score=0.9,
                      section="Intro > Overview", version=2, url=None)
    assert retrieval_started_event().event == "retrieval_started"
    assert sources_event([src]).data == {"sources": [{
        "marker": 1, "document_id": "d-1", "filename": "a.pdf", "page": 2,
        "chunk_index": 0, "score": 0.9, "snippet": "text",
        "section": "Intro > Overview", "version": 2, "url": None}]}
    assert citations_event([cit]).data == {"citations": [{
        "marker": 1, "document_id": "d-1", "chunk_ref": "d-1:2:0",
        "page": 2, "score": 0.9, "section": "Intro > Overview", "version": 2,
        "url": None}]}
    done = done_event(message_id="m-1", prompt_tokens=10, completion_tokens=2,
                      no_answer=False, grounding="documents")
    assert done.event == "done"
    assert done.data == {"message_id": "m-1", "prompt_tokens": 10,
                         "completion_tokens": 2, "no_answer": False,
                         "grounding": "documents", "validation_failed": False}
    assert error_event("boom").data == {"detail": "boom"}


def test_done_event_carries_grounding() -> None:
    e = done_event(
        message_id="m1", prompt_tokens=1, completion_tokens=2,
        no_answer=False, grounding="general",
    )
    assert e.data["grounding"] == "general"


def test_agent_step_event_shape() -> None:
    e = agent_step_event(n=2, tool="search", query="muster point")
    assert e.event == "agent_step"
    assert e.data == {"n": 2, "tool": "search", "query": "muster point"}


def test_source_ref_carries_web_url() -> None:
    src = SourceRef(marker=1, document_id="", filename="ISO 45001 overview", page=0,
                    chunk_index=0, score=0.0, snippet="text", section=None, version=0,
                    url="https://x.test")
    assert asdict(src)["url"] == "https://x.test"


def test_done_event_defaults_validation_failed_false() -> None:
    e = done_event(message_id="m1", prompt_tokens=1, completion_tokens=2,
                   no_answer=False, grounding="documents")
    assert e.data["validation_failed"] is False


def test_done_event_carries_validation_failed_true() -> None:
    e = done_event(message_id="m1", prompt_tokens=1, completion_tokens=2,
                   no_answer=False, grounding="documents", validation_failed=True)
    assert e.data["validation_failed"] is True
