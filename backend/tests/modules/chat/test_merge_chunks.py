from uuid import uuid4

from ragz.modules.chat.service import cap_chunks_by_tokens, merge_chunks
from ragz.modules.retrieval.service import RetrievedChunk


def chunk(doc_id, page: int, idx: int, text: str, score: float = 0.5) -> RetrievedChunk:  # type: ignore[no-untyped-def]
    return RetrievedChunk(document_id=doc_id, page=page, chunk_index=idx,
                          text=text, score=score)


def test_merge_dedupes_across_groups_keeping_first_occurrence() -> None:
    d1, d2 = uuid4(), uuid4()
    pinned = [chunk(d1, 1, 0, "pinned text", score=1.0)]
    retrieved = [chunk(d1, 1, 0, "pinned text", score=0.9), chunk(d2, 2, 1, "other")]
    merged = merge_chunks(pinned, retrieved)
    assert [(c.document_id, c.score) for c in merged] == [(d1, 1.0), (d2, 0.5)]


def test_merge_preserves_group_priority_order() -> None:
    d = uuid4()
    a, b, c = chunk(d, 1, 0, "a"), chunk(d, 1, 1, "b"), chunk(d, 2, 2, "c")
    assert merge_chunks([b], [a], [c]) == [b, a, c]


def test_cap_chunks_by_tokens_keeps_prefix() -> None:
    d = uuid4()
    small = chunk(d, 1, 0, "tiny")
    huge = chunk(d, 1, 1, "word " * 5000)
    assert cap_chunks_by_tokens([small, huge], 100, None) == [small]
    assert cap_chunks_by_tokens([small], 0, None) == []
