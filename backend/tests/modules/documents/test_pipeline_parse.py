import io

import pytest
from docx import Document as DocxBuilder

from raghub.modules.documents.pipeline import IngestFailure, parse_bytes


def build_docx() -> bytes:
    d = DocxBuilder()
    d.add_heading("Flux Capacitor Manual", level=1)
    d.add_paragraph("The flux capacitor requires 1.21 gigawatts of power.")
    d.add_heading("Billing", level=1)
    d.add_paragraph("Invoice 0231 covers the plutonium delivery.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_parse_docx_extracts_blocks_with_kinds() -> None:
    blocks = parse_bytes(build_docx(), "manual.docx")
    texts = " ".join(b.text for b in blocks)
    assert "1.21 gigawatts" in texts and "Invoice 0231" in texts
    assert any(b.kind == "heading" for b in blocks)
    assert all(b.page >= 1 for b in blocks)


def test_parse_txt_fast_path() -> None:
    blocks = parse_bytes(b"para one\n\npara two", "notes.txt")
    assert [b.text for b in blocks] == ["para one", "para two"]
    assert all(b.kind == "text" and b.page == 1 for b in blocks)


def test_empty_file_fails_with_reason() -> None:
    with pytest.raises(IngestFailure, match="empty"):
        parse_bytes(b"", "empty.txt")


def test_unsupported_format_fails_with_reason() -> None:
    with pytest.raises(IngestFailure):
        parse_bytes(b"\x00\x01garbage", "weird.xyz")


def _pptx_bytes() -> bytes:
    from pptx import Presentation

    prs = Presentation()
    for i in (1, 2):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = f"Safety Briefing Part {i}"
        slide.placeholders[1].text = (
            f"Slide {i} body: always wear certified PPE inside zone {i}."
        )
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_parse_pptx_slides_become_pages() -> None:
    blocks = parse_bytes(_pptx_bytes(), "briefing.pptx")
    assert {b.page for b in blocks} == {1, 2}  # slide number = page number
    joined = " ".join(b.text for b in blocks)
    assert "certified PPE" in joined
    # Slide titles surface as headings (feeds Task 3's section trail).
    assert any(b.kind == "heading" and "Safety Briefing" in b.text for b in blocks)
