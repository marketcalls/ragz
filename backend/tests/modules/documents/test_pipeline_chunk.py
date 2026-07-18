from raghub.modules.documents.pipeline import Chunk, PageBlock, chunk_blocks


def blk(text: str, page: int = 1, kind: str = "text") -> PageBlock:
    return PageBlock(page=page, text=text, kind=kind)


def test_empty_input_returns_empty() -> None:
    assert chunk_blocks([]) == []


def test_single_short_block_single_chunk() -> None:
    chunks = chunk_blocks([blk("hello world", page=3)])
    assert chunks == [Chunk(text="hello world", page=3, chunk_index=0)]


def test_long_text_splits_with_overlap() -> None:
    words = " ".join(f"word{i}" for i in range(1200))  # ~8000 chars
    chunks = chunk_blocks([blk(words)], target_chars=2000, overlap_ratio=0.15)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 2600 for c in chunks)  # target + slack, never unbounded
    # 15% overlap: the head of chunk N+1 repeats the tail of chunk N
    tail_words = chunks[0].text.split()[-10:]
    assert " ".join(tail_words) in chunks[1].text


def test_table_kept_whole_even_when_oversized() -> None:
    table = ("| a | b |\n" * 400).strip()  # ~3600 chars, beyond target
    chunks = chunk_blocks([blk("intro text"), blk(table, kind="table"), blk("outro")])
    table_chunks = [c for c in chunks if c.text == table]
    assert len(table_chunks) == 1  # never split, never merged


def test_heading_starts_new_chunk_when_buffer_substantial() -> None:
    body = "x" * 1200  # above target/2
    chunks = chunk_blocks([blk(body), blk("Chapter Two", kind="heading"), blk("more text")])
    assert len(chunks) == 2
    assert chunks[1].text.startswith("Chapter Two")


def test_indices_sequential_and_pages_tracked() -> None:
    chunks = chunk_blocks([blk("a", page=1), blk("b" * 3000, page=2, kind="table"),
                           blk("c", page=3)])
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].page == 1
