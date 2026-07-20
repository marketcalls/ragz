import pytest

from raghub.modules.chat.router import classify_query, is_ambiguous_for_escalation, should_escalate


@pytest.mark.parametrize(
    "content",
    [
        "Hi",
        "hi",
        "Hii",
        "hiii!",
        "Hello",
        "hello!",
        "Hey",
        "heyy",
        "Yo",
        "yoo.",
        "Good morning",
        "good afternoon!",
        "Good evening.",
        "Namaste",
        "namaste!",
        "Thanks",
        "thank you",
        "Thx",
        "OK",
        "okay",
        "Cool",
        "great",
        "nice",
        "Bye",
        "goodbye",
        "see you",
        "How are you",
        "how are you?",
        "What's up",
        "whats up",
        "Who are you",
        "What can you do",
        "help",
        "What is your name",
        "what's your name?",
        "whats your name",
        "help!",
        "hi 👋",
        "thanks!!",
    ],
)
def test_classify_query_conversational_cases(content: str) -> None:
    assert classify_query(content) == "conversational"


@pytest.mark.parametrize(
    "content",
    [
        "hi there, what does the contract say",
        "what was our revenue last quarter?",
        "summarize the attached invoice",
        "help me understand section 4.2 of the msa",
        "thanks for the report, what's the total on page 3?",
        "hello, does the contract mention indemnification?",
        "",
        "   ",
        "hi hi hi hi hi hi hi hi hi hi hi hi hi",  # long, > 40 chars
        "who are you referring to in paragraph two?",
    ],
)
def test_classify_query_retrieval_cases(content: str) -> None:
    assert classify_query(content) == "retrieval"


def test_classify_query_retrieval_for_document_question_prefixed_with_greeting() -> None:
    assert classify_query("hi there, what does the contract say") == "retrieval"


def test_classify_query_empty_or_whitespace_is_retrieval() -> None:
    assert classify_query("") == "retrieval"
    assert classify_query("   ") == "retrieval"


def test_classify_query_long_greeting_like_string_is_retrieval() -> None:
    assert classify_query("hi hi hi hi hi hi hi hi hi hi hi hi hi") == "retrieval"


def test_simple_lookup_does_not_escalate() -> None:
    # The load-test gate depends on this staying False (design §9).
    assert not should_escalate("What is the muster point?")
    assert not should_escalate("summarize the safety policy")
    assert not should_escalate("hello")
    assert not should_escalate("")


def test_two_question_marks_escalate() -> None:
    assert should_escalate("What is the muster point? And who approves changes?")


def test_two_distinct_interrogatives_escalate() -> None:
    assert should_escalate("What changed and when was it approved")


def test_comparatives_escalate() -> None:
    assert should_escalate("Compare the evacuation steps in v1 versus v2")
    assert should_escalate("difference between the old and new policy")


def test_dates_escalate() -> None:
    # Metadata-dimension trigger: years and ISO dates suggest revision_date-style
    # filtering the single-shot pass can't do.
    assert should_escalate("Which memos mention the 2026 budget")
    assert should_escalate("anything revised after 2026-01-01")


def test_metadata_field_names_escalate() -> None:
    fields = ["department", "doc_type", "revision_date"]
    assert should_escalate("show the HSE department procedures", fields)
    assert should_escalate("filter by doc type manual", fields)  # underscore -> space
    assert not should_escalate("show the HSE procedures", fields)


def test_field_names_only_match_when_supplied() -> None:
    assert not should_escalate("show the HSE department procedures")


def test_short_question_is_not_ambiguous() -> None:
    assert not is_ambiguous_for_escalation("What is the muster point?")
    assert not is_ambiguous_for_escalation("hello")
    assert not is_ambiguous_for_escalation("")


def test_long_single_clause_question_is_ambiguous() -> None:
    assert is_ambiguous_for_escalation(
        "Summarize the current evacuation procedure for the north building complex"
    )
