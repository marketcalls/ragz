"""Auditor/Gatekeeper/escalation-classifier prompts and lenient parsers
(Phase 3 §3 + §1's utility tiebreak). Pure — no I/O, no session."""

from raghub.modules.chat.prompting import PromptSource
from raghub.modules.chat.validation import (
    AuditorScores,
    GatekeeperVerdict,
    build_auditor_messages,
    build_escalation_messages,
    build_gatekeeper_messages,
    parse_auditor_scores,
    parse_escalation_verdict,
    parse_gatekeeper_verdict,
)

_SOURCES = [PromptSource(marker=1, filename="policy.pdf", page=2, text="Muster at gate B.")]


def test_auditor_messages_embed_data_blocks_and_wrapped_answer() -> None:
    msgs = build_auditor_messages(question="Where is the muster point?",
                                  answer="[1] Gate B.", sources=_SOURCES)
    assert msgs[0]["role"] == "system" and "grounding_score" in msgs[0]["content"]
    assert '<data id="1"' in msgs[1]["content"]
    assert "<answer>" in msgs[1]["content"] and "[1] Gate B." in msgs[1]["content"]


def test_auditor_messages_handle_empty_sources() -> None:
    msgs = build_auditor_messages(question="q", answer="a", sources=[])
    assert "no source excerpts" in msgs[1]["content"]


def test_parse_auditor_scores_happy_path() -> None:
    scores = parse_auditor_scores('{"grounding_score": 0.9, "completeness_score": 1.0}')
    assert scores == AuditorScores(grounding_score=0.9, completeness_score=1.0)


def test_parse_auditor_scores_clamps_out_of_range() -> None:
    scores = parse_auditor_scores('{"grounding_score": 1.4, "completeness_score": -0.2}')
    assert scores == AuditorScores(grounding_score=1.0, completeness_score=0.0)


def test_parse_auditor_scores_malformed_is_none() -> None:
    assert parse_auditor_scores("not json at all") is None
    assert parse_auditor_scores('{"grounding_score": "n/a"}') is None
    assert parse_auditor_scores("") is None


def test_gatekeeper_messages_label_candidate_answer() -> None:
    msgs = build_gatekeeper_messages(question="q", answer="a", sources=_SOURCES)
    assert "Candidate answer" in msgs[1]["content"]


def test_parse_gatekeeper_verdict_pass_and_fail() -> None:
    passed = parse_gatekeeper_verdict('{"passed": true, "critique": ""}')
    assert passed == GatekeeperVerdict(True, "")
    v = parse_gatekeeper_verdict('{"passed": false, "critique": "unsupported claim"}')
    assert v == GatekeeperVerdict(False, "unsupported claim")


def test_parse_gatekeeper_verdict_malformed_fails_open() -> None:
    assert parse_gatekeeper_verdict("garbage") == GatekeeperVerdict(True, "")
    assert parse_gatekeeper_verdict('{"passed": "yes"}') == GatekeeperVerdict(True, "")


def test_escalation_messages_wrap_question() -> None:
    msgs = build_escalation_messages("What changed and when was it approved")
    assert "<question>" in msgs[1]["content"]


def test_parse_escalation_verdict() -> None:
    assert parse_escalation_verdict('{"escalate": true}') is True
    assert parse_escalation_verdict('{"escalate": false}') is False
    assert parse_escalation_verdict("garbage") is False
    assert parse_escalation_verdict('{"escalate": "true"}') is False  # must be a JSON bool
