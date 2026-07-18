from raghub.modules.chat.prompting import CANNONBALL_MARKER, cannonball, count_tokens


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
