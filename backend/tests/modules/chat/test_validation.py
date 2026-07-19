"""Auditor/Gatekeeper/escalation-classifier prompts and lenient parsers
(Phase 3 §3 + §1's utility tiebreak). Pure — no I/O, no session."""

from raghub.modules.chat.llm import LLMCompletion, LLMUsage
from raghub.modules.chat.prompting import PromptSource
from raghub.modules.chat.validation import (
    AuditorScores,
    GatekeeperVerdict,
    GatekeptAnswer,
    build_auditor_messages,
    build_escalation_messages,
    build_gatekeeper_messages,
    parse_auditor_scores,
    parse_escalation_verdict,
    parse_gatekeeper_verdict,
    synthesize_with_gatekeeper,
)
from tests.conftest import FakeCompleter

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


# --- synthesize_with_gatekeeper (Task 6) ------------------------------------


def _rebuild(critique: str | None) -> list[dict]:  # type: ignore[type-arg]
    return [{"role": "system", "content": f"base + {critique}"}, {"role": "user", "content": "q"}]


async def test_passing_candidate_streams_as_is() -> None:
    completer = FakeCompleter([
        LLMCompletion(text="[1] Gate B.", tool_calls=[], usage=LLMUsage(50, 10)),  # synth
        LLMCompletion(text='{"passed": true, "critique": ""}', tool_calls=[],
                      usage=LLMUsage(20, 5)),  # judge
    ])
    out = await synthesize_with_gatekeeper(
        completer, chat_model_name="chat-m", utility_model_name="util-m",
        prompt=[{"role": "user", "content": "q"}], question="q?", sources=[],
        system_prompt_override=None, rebuild_prompt=_rebuild,
    )
    assert out == GatekeptAnswer(
        text="[1] Gate B.", usage=LLMUsage(50, 10), validation_failed=False,
        extra_prompt_tokens=20, extra_completion_tokens=5,
    )
    assert completer.calls[0]["model"] == "chat-m"
    assert completer.calls[1]["model"] == "util-m"
    assert len(completer.calls) == 2  # no regeneration spent


async def test_failing_candidate_regenerates_once_and_flags() -> None:
    completer = FakeCompleter([
        LLMCompletion(text="unsupported claim.", tool_calls=[], usage=LLMUsage(50, 10)),  # synth 1
        LLMCompletion(text='{"passed": false, "critique": "fabricated"}', tool_calls=[],
                      usage=LLMUsage(20, 5)),  # judge
        LLMCompletion(text="[1] Gate B, revised.", tool_calls=[], usage=LLMUsage(60, 12)),  # synth2
    ])
    out = await synthesize_with_gatekeeper(
        completer, chat_model_name="chat-m", utility_model_name="util-m",
        prompt=[{"role": "user", "content": "q"}], question="q?", sources=[],
        system_prompt_override="Be terse.", rebuild_prompt=_rebuild,
    )
    assert out.text == "[1] Gate B, revised."
    assert out.validation_failed is True
    assert out.usage == LLMUsage(60, 12)  # the WINNING (second) attempt's own usage
    assert out.extra_prompt_tokens == 20 + 50   # judge + discarded first synth
    assert out.extra_completion_tokens == 5 + 10
    assert len(completer.calls) == 3
    retry_system = completer.calls[2]["messages"][0]["content"]  # type: ignore[index]
    assert "Be terse." in retry_system and "fabricated" in retry_system


async def test_malformed_judge_output_fails_open_no_regeneration() -> None:
    completer = FakeCompleter([
        LLMCompletion(text="[1] Gate B.", tool_calls=[], usage=LLMUsage(50, 10)),
        LLMCompletion(text="garbage, not json", tool_calls=[], usage=LLMUsage(20, 5)),
    ])
    out = await synthesize_with_gatekeeper(
        completer, chat_model_name="chat-m", utility_model_name="util-m",
        prompt=[{"role": "user", "content": "q"}], question="q?", sources=[],
        system_prompt_override=None, rebuild_prompt=_rebuild,
    )
    assert out.validation_failed is False and len(completer.calls) == 2
