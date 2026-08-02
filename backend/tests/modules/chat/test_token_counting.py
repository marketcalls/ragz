import pytest

import ragz.modules.chat.prompting as prompting
from ragz.modules.chat.prompting import (
    CANNONBALL_MARKER,
    cannonball,
    count_tokens,
    warm_token_encoder,
)


@pytest.fixture(autouse=True)
def _clean_encoding_cache() -> None:
    """Fix 2 tests deliberately break tiktoken acquisition and latch the
    module-level failure sentinel; reset both before and after every test in
    this file so that breakage never leaks into other tests (in this file or
    -- since pytest runs a single process -- any other)."""
    prompting._encoding.cache_clear()
    prompting._ENCODING_UNAVAILABLE = False
    yield
    prompting._encoding.cache_clear()
    prompting._ENCODING_UNAVAILABLE = False


def test_count_tokens_basic() -> None:
    # cl100k_base is a frozen encoding: these counts are stable across versions.
    assert count_tokens("") == 0
    assert count_tokens("hello world") == 2


def test_count_tokens_beats_char_estimate_for_cjk() -> None:
    # The old len//4 estimate said 3 tokens for 12 CJK chars; reality is far more.
    text = "日本語のテキストです。" * 1
    assert count_tokens(text) > len(text) // 4


def test_count_tokens_unknown_model_falls_back() -> None:
    # LiteLLM-style names are not tiktoken model names -> cl100k_base fallback.
    assert count_tokens("hello world", "ollama/llama3") == count_tokens("hello world")


def test_count_tokens_special_tokens_are_data_not_specials() -> None:
    # Document text may contain literal "<|endoftext|>"; counting must not raise
    # (disallowed_special=()) - it's data, not a control token (iron rule 5 spirit).
    assert count_tokens("<|endoftext|>") > 0


def test_cannonball_passthrough_when_it_fits() -> None:
    assert cannonball("short text", 100) == "short text"


def test_cannonball_truncates_middle_out() -> None:
    text = " ".join(f"w{i}" for i in range(2000))
    out = cannonball(text, 120)
    assert CANNONBALL_MARKER in out
    assert out.startswith("w0")            # head preserved
    assert out.rstrip().endswith("w1999")  # tail preserved
    # decode/re-encode can merge boundary tokens; allow tiny drift, never blowout
    assert count_tokens(out) <= 120 + 4


def test_cannonball_tiny_budget_still_returns_something() -> None:
    out = cannonball("x " * 5000, 4)
    assert CANNONBALL_MARKER in out
    assert len(out) < 200


def _break_tiktoken(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Makes both tiktoken entry points raise, as they would air-gapped or
    with the encoding download otherwise failing. Returns the call log so
    tests can assert the failure is latched (no retry per call)."""
    calls: list[int] = []

    def _boom(*args: object, **kwargs: object) -> None:
        calls.append(1)
        raise OSError("network unreachable")

    monkeypatch.setattr(prompting.tiktoken, "get_encoding", _boom)
    monkeypatch.setattr(prompting.tiktoken, "encoding_for_model", _boom)
    return calls


def test_count_tokens_falls_back_to_char_estimate_when_tiktoken_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_tiktoken(monkeypatch)
    text = "hello world, this is the char-based fallback estimate"
    assert count_tokens(text) == max(1, len(text) // 4)
    assert count_tokens("") == 1  # max(1, 0) -- never zero, even for empty text


def test_tiktoken_failure_latches_and_does_not_retry_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _break_tiktoken(monkeypatch)
    count_tokens("first call")
    count_tokens("second call", "some-other-model-hint")
    count_tokens("third call")
    assert len(calls) == 1  # only the first failing attempt ever touches tiktoken


def test_cannonball_falls_back_to_char_split_when_tiktoken_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_tiktoken(monkeypatch)
    text = " ".join(f"w{i}" for i in range(2000))
    out = cannonball(text, 40)
    assert CANNONBALL_MARKER in out
    assert out.startswith("w0")            # head preserved
    assert out.rstrip().endswith("w1999")  # tail preserved
    assert len(out) < len(text)


def test_cannonball_char_fallback_passthrough_when_it_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_tiktoken(monkeypatch)
    assert cannonball("short text", 100) == "short text"


def test_warm_token_encoder_is_callable_and_never_raises_when_tiktoken_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_tiktoken(monkeypatch)
    warm_token_encoder()  # must not raise even when the encoding download fails


def test_warm_token_encoder_is_callable_in_the_normal_case() -> None:
    warm_token_encoder()  # no exception; primes the real encoding cache
