"""Deterministic query router (PRD CHAT-3, Phase 1 slice).

`classify_query` decides whether a user turn needs document retrieval at
all. It is intentionally cheap and rule-based - a pure function with no I/O,
no model calls, no session. Phase 3 of the PRD is expected to replace this
implementation with an LLM-based router; keep the signature
(`classify_query(content: str) -> Literal["conversational", "retrieval"]`)
stable so callers don't need to change when that happens.

Design bias: false-retrieval (sending small talk through the retrieval path)
is harmless - it just costs an unnecessary search. False-conversational
(skipping retrieval for a real document question) silently loses citations
and can produce a confidently wrong answer. So the "conversational" bucket
is kept deliberately tight: short, whole-string matches against a small set
of greetings/acknowledgements/courtesy/meta phrases. Anything that doesn't
clearly match falls through to "retrieval".
"""

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

QueryClass = Literal["conversational", "retrieval"]

_MAX_CONVERSATIONAL_LEN = 40

# Whole-string patterns/sets to match AFTER normalization (whitespace
# collapsed, lowercased, trailing punctuation/emoji stripped). Matching is
# against the ENTIRE normalized string, never a substring - "hi there, what
# does the contract say" must not match just because it starts with "hi".
_GREETING_RE = re.compile(
    r"^(hi+|hey+|yo+|hello+|good (morning|afternoon|evening)|namaste)$"
)
_CLOSER_EXACT = {
    "thanks", "thank you", "thx", "ok", "okay", "cool", "great", "nice",
    "bye", "goodbye", "see you",
}
_COURTESY_EXACT = {"how are you", "what's up", "whats up"}
_META_EXACT = {
    "who are you", "what can you do", "help",
    "what is your name", "what's your name", "whats your name",
}


def _strip_trailing_decoration(text: str) -> str:
    """Strip trailing whitespace, punctuation, and emoji/symbol characters.

    Lets "hi!", "hi.", "hi 👋", "thanks!!" normalize down to their bare
    phrase before matching. Only trims from the end, so internal punctuation
    (e.g. the apostrophe in "what's up") is untouched.
    """
    while text:
        ch = text[-1]
        if ch.isspace():
            text = text[:-1]
        elif unicodedata.category(ch).startswith(("P", "S")):
            # P* = punctuation (.!?,;:'"-etc), S* = symbols (incl. emoji).
            text = text[:-1]
        else:
            break
    return text


def classify_query(content: str) -> QueryClass:
    """Classify a user chat turn as "conversational" or "retrieval".

    "conversational" means: skip retrieval and answer directly from a short
    conversational system prompt. Reserved for short (<=40 char) small talk -
    greetings, acknowledgements/closers, courtesy check-ins, and
    assistant-meta questions. Everything else, including anything that only
    starts or ends with one of those phrases, is "retrieval".
    """
    normalized = _strip_trailing_decoration(" ".join(content.split()).lower())
    if not normalized or len(normalized) > _MAX_CONVERSATIONAL_LEN:
        return "retrieval"
    if (
        _GREETING_RE.match(normalized)
        or normalized in _CLOSER_EXACT
        or normalized in _COURTESY_EXACT
        or normalized in _META_EXACT
    ):
        return "conversational"
    return "retrieval"


_INTERROGATIVE_RE = re.compile(
    r"\b(who|whom|whose|what|which|when|where|why|how)\b", re.IGNORECASE
)
_COMPARATIVE_RE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference between|differences between)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def should_escalate(content: str, metadata_field_names: Sequence[str] = ()) -> bool:
    """Router v2 (Phase 3 design §1): does this question warrant the agent loop?

    Deterministic and cheap, like classify_query. Triggers:
    - multiple question marks, or >=2 DISTINCT interrogative words (multi-part
      questions: "what changed and when was it approved");
    - comparative phrasing (compare/versus/difference between);
    - metadata dimensions: a workspace metadata field name (underscores read as
      spaces), a calendar year, or an ISO date.

    Bias: false-escalate costs one planner round-trip's tokens but still
    answers correctly; false-skip is rescued by stream_reply's post-retrieval
    weak-results trigger. Design D5/§1's utility-model tiebreak for ambiguous
    cases is deliberately ABSENT until Plan J designates a utility model —
    Plan J hooks in at this function's call site, not by changing it.
    """
    text = " ".join(content.split()).lower()
    if not text:
        return False
    if text.count("?") >= 2:
        return True
    if _COMPARATIVE_RE.search(text):
        return True
    if len({m.group(1).lower() for m in _INTERROGATIVE_RE.finditer(text)}) >= 2:
        return True
    if _YEAR_RE.search(text) or _ISO_DATE_RE.search(text):
        return True
    return any(
        name and name.replace("_", " ").lower() in text
        for name in metadata_field_names
    )


_AMBIGUOUS_MIN_WORDS = 10


def is_ambiguous_for_escalation(content: str) -> bool:
    """Cheap pre-filter for design §1's utility-model tiebreak. Only
    questions at least this long are worth spending a classifier call on at
    all - should_escalate already resolved the confident cases (both ways),
    and short questions are virtually always single-shot; spending a model
    call on every short question would burn budget for no benefit given the
    escalate-anyway rescue at stream_reply's post-retrieval trigger.

    Deliberately independent of should_escalate's verdict: this function
    alone only answers "is this substantial enough for a classifier call to
    plausibly be worth it" -- it is the CALLER's job (stream_reply) to check
    that should_escalate already said False before spending that call.
    """
    return len(content.split()) >= _AMBIGUOUS_MIN_WORDS
