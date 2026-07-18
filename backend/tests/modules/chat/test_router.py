import pytest

from raghub.modules.chat.router import classify_query


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
