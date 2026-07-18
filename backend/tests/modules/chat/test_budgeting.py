from raghub.modules.chat.prompting import (
    CANNONBALL_MARKER,
    SYSTEM_PROMPT,
    TRUNCATION_NOTE,
    PromptSource,
    build_conversational_messages,
    build_messages,
    count_tokens,
    fit_sources,
    split_budget,
)


def src(marker: int, text: str) -> PromptSource:
    return PromptSource(marker=marker, filename=f"f{marker}.pdf", page=1, text=text)


def test_split_budget_fractions_sum_to_budget() -> None:
    s = split_budget(8000)
    assert s.system == 1200          # 15%
    assert s.sources == 5600         # 70%
    assert s.system + s.sources + s.history == 8000


def test_fit_sources_keeps_prefix_that_fits() -> None:
    sources = [src(1, "alpha " * 50), src(2, "bravo " * 50), src(3, "charlie " * 5000)]
    kept = fit_sources(sources, 300)
    assert [s.marker for s in kept] == [1, 2]  # 3 does not fit and is dropped


def test_fit_sources_cannonballs_a_lone_oversized_first_source() -> None:
    kept = fit_sources([src(1, "delta " * 5000)], 200)
    assert len(kept) == 1
    assert CANNONBALL_MARKER in kept[0].text
    assert kept[0].marker == 1


def test_fit_sources_empty_and_zero_budget() -> None:
    assert fit_sources([], 1000) == []
    assert fit_sources([src(1, "echo " * 500)], 0) == []


def test_build_messages_appends_override_after_base_system_prompt() -> None:
    msgs = build_messages(
        sources=[src(1, "revenue was 12M")], history=[], user_query="q",
        budget=8000, system_prompt_override="Answer in formal English.",
    )
    system = msgs[0]["content"]
    assert system.startswith(SYSTEM_PROMPT)          # base rules first, verbatim
    assert "Answer in formal English." in system
    assert system.index(SYSTEM_PROMPT) < system.index("Answer in formal English.")


def test_build_messages_override_is_capped_by_system_share() -> None:
    huge = "Obey this style guide. " + ("blah " * 20000)
    msgs = build_messages(
        sources=[src(1, "x")], history=[], user_query="q",
        budget=8000, system_prompt_override=huge,
    )
    system = msgs[0]["content"]
    assert system.startswith(SYSTEM_PROMPT)
    assert CANNONBALL_MARKER in system               # override cannonballed
    assert count_tokens(system) <= split_budget(8000).system + 8


def test_build_messages_no_override_unchanged_shape() -> None:
    msgs = build_messages(sources=[src(1, "x")], history=[("user", "a"), ("assistant", "b")],
                          user_query="q", budget=8000)
    assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert msgs[-1]["role"] == "user"
    assert "Question: q" in msgs[-1]["content"]


def test_history_truncates_oldest_with_note() -> None:
    history = [(("user", "assistant")[i % 2], f"turn {i} " + "pad " * 200) for i in range(10)]
    msgs = build_messages(sources=[src(1, "x")], history=history, user_query="q", budget=2000)
    contents = [m["content"] for m in msgs]
    joined = "\n".join(contents)
    assert "turn 9" in joined                        # newest kept
    assert "turn 0" not in joined                    # oldest dropped
    note = next(c for c in contents if c.startswith("[Earlier conversation truncated"))
    assert note == TRUNCATION_NOTE.format(n=int(note.split()[3]))


def test_newest_history_turn_alone_oversized_is_cannonballed_not_dropped() -> None:
    history = [("user", "pinpoint start " + "pad " * 8000 + " pinpoint end")]
    msgs = build_messages(sources=[src(1, "x")], history=history, user_query="q", budget=3000)
    hist = [m for m in msgs if m["role"] == "user"][:-1]
    assert len(hist) == 1
    assert CANNONBALL_MARKER in hist[0]["content"]
    assert "pinpoint start" in hist[0]["content"] and "pinpoint end" in hist[0]["content"]


def test_conversational_builder_takes_override_too() -> None:
    msgs = build_conversational_messages(
        history=[], user_query="hi", budget=8000,
        system_prompt_override="Sign off as AcmeBot.",
    )
    assert "Sign off as AcmeBot." in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "hi"}
