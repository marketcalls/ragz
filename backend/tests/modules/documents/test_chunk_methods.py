from ragz.modules.documents.pipeline import Chunk, PageBlock, chunk_blocks, chunk_document


def blk(text: str, page: int = 1, kind: str = "text", level: int | None = None) -> PageBlock:
    return PageBlock(page=page, text=text, kind=kind, level=level)


def _multi_page_blocks() -> list[PageBlock]:
    return [
        PageBlock(page=1, text="Fire Safety Manual", kind="heading", level=0),
        PageBlock(page=1, text="Evacuation", kind="heading", level=1),
        PageBlock(page=1, text="Assembly points are listed below.", kind="text"),
        PageBlock(page=2, text="Alarms", kind="heading", level=1),
        PageBlock(page=2, text="Test alarms weekly.", kind="text"),
    ]


def test_heading_method_matches_chunk_blocks_exactly() -> None:
    blocks = _multi_page_blocks()
    assert chunk_document(blocks, method="heading") == chunk_blocks(blocks)


def test_heading_method_matches_chunk_blocks_with_table() -> None:
    table = ("| a | b |\n" * 400).strip()
    blocks = [blk("intro text"), blk(table, kind="table"), blk("outro")]
    assert chunk_document(blocks, method="heading") == chunk_blocks(blocks)


def test_fixed_yields_target_char_windows_with_overlap() -> None:
    words = " ".join(f"word{i}" for i in range(1200))  # ~8000 chars
    chunks = chunk_document(
        [blk(words, page=5)], method="fixed", target_chars=2000, overlap_ratio=0.15
    )
    assert len(chunks) >= 3
    assert all(c.page == 5 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    # 15% overlap: tail of chunk N reappears in the head of chunk N+1
    tail = chunks[0].text[-100:]
    assert tail[:50] in chunks[1].text or tail in (chunks[0].text + chunks[1].text)


def test_fixed_empty_blocks_returns_empty() -> None:
    assert chunk_document([], method="fixed") == []
    assert chunk_document([blk("")], method="fixed") == []


def test_page_yields_one_chunk_per_distinct_page() -> None:
    blocks = [
        blk("a", page=1),
        blk("b", page=1),
        blk("c", page=2),
        blk("d", page=3),
    ]
    chunks = chunk_document(blocks, method="page")
    assert [c.page for c in chunks] == [1, 2, 3]
    assert chunks[0].text == "a\n\nb"
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_page_skips_empty_pages() -> None:
    blocks = [blk("", page=1), blk("real content", page=2)]
    chunks = chunk_document(blocks, method="page")
    assert len(chunks) == 1
    assert chunks[0].page == 2


def test_table_qa_one_chunk_per_table_block() -> None:
    table = "| a | b |\n| 1 | 2 |"
    blocks = [blk("intro"), blk(table, kind="table", page=2), blk("outro")]
    chunks = chunk_document(blocks, method="table_qa")
    table_chunks = [c for c in chunks if c.text == table]
    assert len(table_chunks) == 1
    assert table_chunks[0].page == 2


def test_table_qa_one_chunk_per_heading_group() -> None:
    blocks = _multi_page_blocks()
    chunks = chunk_document(blocks, method="table_qa")
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    evac = next(c for c in chunks if "Assembly points" in c.text)
    assert evac.section == "Fire Safety Manual > Evacuation"
    alarm = next(c for c in chunks if "Test alarms" in c.text)
    assert alarm.section == "Fire Safety Manual > Alarms"


def test_table_qa_mixed_tables_and_headings() -> None:
    table = "| x | y |\n| 1 | 2 |"
    blocks = [
        blk("Chapter One", kind="heading", level=1),
        blk("intro text", page=1),
        blk(table, kind="table", page=1),
        blk("Chapter Two", kind="heading", level=1),
        blk("more text", page=2),
    ]
    chunks = chunk_document(blocks, method="table_qa")
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    table_chunks = [c for c in chunks if c.text == table]
    assert len(table_chunks) == 1
    assert table_chunks[0].section == "Chapter One"
    tail = next(c for c in chunks if "more text" in c.text)
    assert tail.section == "Chapter Two"


def test_chunk_document_returns_chunk_instances() -> None:
    chunks = chunk_document([blk("hello world")], method="page")
    assert all(isinstance(c, Chunk) for c in chunks)
