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
_META_EXACT = {"who are you", "what can you do", "help"}


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
