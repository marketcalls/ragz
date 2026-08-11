from ragz.modules.documents.parsers import _markdown_to_blocks


def test_headings_map_to_heading_blocks_with_level():
    blocks = _markdown_to_blocks("# Title\n\n## Section A\n\nbody text here\n")
    kinds = [(b.kind, b.level, b.text) for b in blocks]
    assert ("heading", 1, "Title") in kinds
    assert ("heading", 2, "Section A") in kinds
    assert any(b.kind == "text" and b.text == "body text here" for b in blocks)
    assert all(b.page == 1 for b in blocks)


def test_gfm_table_becomes_one_table_block():
    md = "intro\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\nafter\n"
    blocks = _markdown_to_blocks(md)
    tables = [b for b in blocks if b.kind == "table"]
    assert len(tables) == 1
    assert "| a | b |" in tables[0].text and "| 1 | 2 |" in tables[0].text
    assert [b.text for b in blocks if b.kind == "text"] == ["intro", "after"]


def test_paragraphs_split_on_blank_lines_and_whitespace_dropped():
    blocks = _markdown_to_blocks("para one\n\n\n   \n\npara two\n")
    assert [b.text for b in blocks] == ["para one", "para two"]
    assert all(b.kind == "text" for b in blocks)


def test_empty_markdown_yields_no_blocks():
    assert _markdown_to_blocks("   \n\n") == []


def test_section_trail_is_derivable_by_existing_chunker():
    # The mapper emits heading blocks; the existing chunk_blocks builds the
    # section trail from them. This locks the integration contract.
    from ragz.modules.documents.pipeline import chunk_blocks
    blocks = _markdown_to_blocks("# H1\n\n## H2\n\nbody\n")
    chunks = chunk_blocks(blocks)
    body = [c for c in chunks if "body" in c.text]
    assert body and body[0].section == "H1 > H2"
