import types
from pathlib import Path

import anydoc
import httpx
import pytest

from ragz.core.app_settings import set_app_setting
from ragz.core.config import Settings
from ragz.modules.documents.parsers import (
    AnydocParser,
    LiteParsePageLimitExceeded,
    LiteParseParser,
    LlamaParseParser,
    _markdown_to_blocks,
    parse_document,
)
from ragz.modules.documents.pipeline import IngestFailure, PageBlock


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    from ragz.modules.secrets.crypto import ensure_kek
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


def _llama_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/upload"):
            return httpx.Response(200, json={"id": "job1"})
        if p.endswith("/job/job1"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        if p.endswith("/job/job1/result/json"):
            return httpx.Response(200, json={"pages": [
                {"page": 1, "md": "Hello"}, {"page": 2, "md": "World"},
            ]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_llamaparse_maps_pages_to_blocks() -> None:
    p = LlamaParseParser(api_key="llx-x", transport=_llama_transport())
    blocks = await p.parse(b"%PDF-1.4 ...", "doc.pdf")
    assert blocks == [
        PageBlock(page=1, text="Hello", kind="text"),
        PageBlock(page=2, text="World", kind="text"),
    ]


async def test_llamaparse_error_status_raises_ingest_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "j"})
        return httpx.Response(200, json={"status": "ERROR"})

    p = LlamaParseParser(api_key="llx-x", transport=httpx.MockTransport(handler))
    with pytest.raises(IngestFailure):
        await p.parse(b"x", "doc.pdf")


async def test_llamaparse_malformed_result_body_raises_ingest_failure() -> None:
    # Upload + status poll succeed, but the job-result response has a
    # non-JSON body -> .json() raises json.JSONDecodeError (a ValueError).
    # This must surface as IngestFailure, not an unhandled worker exception.
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/upload"):
            return httpx.Response(200, json={"id": "job1"})
        if p.endswith("/job/job1"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        if p.endswith("/job/job1/result/json"):
            return httpx.Response(200, text="not json")
        return httpx.Response(404)

    p = LlamaParseParser(api_key="llx-x", transport=httpx.MockTransport(handler))
    with pytest.raises(IngestFailure):
        await p.parse(b"x", "doc.pdf")


async def test_parse_document_defaults_to_liteparse(
    session, settings, monkeypatch
) -> None:
    async def _fake_parse(self, data, filename):
        return [PageBlock(page=7, text="liteparse default", kind="text")]

    monkeypatch.setattr(LiteParseParser, "parse", _fake_parse)
    blocks = await parse_document(session, settings, data=b"pdf", filename="manual.pdf")
    assert blocks == [PageBlock(page=7, text="liteparse default", kind="text")]


async def test_parse_document_docling_when_selected(session, settings) -> None:
    # Explicit docling selection still works, unaffected by the new default.
    await set_app_setting(session, "document_parser", "docling")
    blocks = await parse_document(
        session, settings, data=b"line one\n\nline two", filename="a.txt"
    )
    assert [b.text for b in blocks] == ["line one", "line two"]


async def test_docling_receives_existing_path_without_buffering(
    session, settings, monkeypatch, tmp_path
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    received = []

    def _fake_parse_bytes(data, filename, **kwargs):  # type: ignore[no-untyped-def]
        received.append(data)
        return [PageBlock(page=1, text="path", kind="text")]

    await set_app_setting(session, "document_parser", "docling")
    monkeypatch.setattr("ragz.modules.documents.parsers.parse_bytes", _fake_parse_bytes)

    blocks = await parse_document(session, settings, data=source, filename=source.name)

    assert received == [source]
    assert blocks == [PageBlock(page=1, text="path", kind="text")]


async def test_default_non_pdf_uses_anydoc_with_existing_path(
    session, settings, monkeypatch, tmp_path
) -> None:
    source = tmp_path / "briefing.pptx"
    source.write_bytes(b"PK synthetic test")
    received = []

    async def _fake_anydoc(self, data, filename):  # type: ignore[no-untyped-def]
        received.append(data)
        return [PageBlock(page=1, text="slides", kind="text")]

    monkeypatch.setattr(AnydocParser, "parse", _fake_anydoc)

    blocks = await parse_document(session, settings, data=source, filename=source.name)

    assert received == [source]
    assert blocks == [PageBlock(page=1, text="slides", kind="text")]


def test_markdown_to_blocks_stamps_given_page() -> None:
    blocks = _markdown_to_blocks(
        "## Heading\n\nbody\n\n| a | b |\n|---|---|\n| 1 | 2 |", page=7
    )
    assert blocks and all(b.page == 7 for b in blocks)
    assert any(b.kind == "heading" for b in blocks)
    assert any(b.kind == "table" for b in blocks)


async def test_liteparse_maps_per_page_markdown_with_real_pages(monkeypatch) -> None:
    class FakeLiteParse:
        def __init__(self, *a, **k) -> None:
            self.target_pages = k["target_pages"]

        def parse(self, data):
            if self.target_pages == "1":
                pages = [types.SimpleNamespace(page_num=1, markdown="")]
            else:
                start, end = map(int, self.target_pages.split("-"))
                pages = []
                for page in range(start, min(end, 8) + 1):
                    markdown = ""
                    if page == 3:
                        markdown = "## HTTP Status Codes\n\ntext on page 3"
                    elif page == 8:
                        markdown = "## Async\n\ntext on page 8"
                    pages.append(types.SimpleNamespace(page_num=page, markdown=markdown))
            return types.SimpleNamespace(total_pages=8, pages=pages)

    monkeypatch.setattr("liteparse.LiteParse", FakeLiteParse)
    blocks = await LiteParseParser().parse(b"x", "d.pdf")
    assert {b.page for b in blocks} == {3, 8}
    headings = {b.text for b in blocks if b.kind == "heading"}
    assert headings == {"HTTP Status Codes", "Async"}


async def test_liteparse_forwards_file_path_without_buffering(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "large.pdf"
    source.write_bytes(b"%PDF-1.7 test")
    received = []

    class FakeLiteParse:
        def __init__(self, *args, **kwargs) -> None:
            self.target_pages = kwargs["target_pages"]

        def parse(self, data):
            received.append(data)
            assert data == source
            return types.SimpleNamespace(
                total_pages=1,
                pages=[types.SimpleNamespace(page_num=1, markdown="page one")],
            )

    monkeypatch.setattr("liteparse.LiteParse", FakeLiteParse)
    blocks = await LiteParseParser().parse(source, source.name)

    assert received == [source]
    assert [(block.page, block.text) for block in blocks] == [(1, "page one")]


async def test_liteparse_empty_result_raises_ingest_failure(monkeypatch) -> None:
    class FakeLiteParse:
        def __init__(self, *a, **k) -> None:
            pass

        def parse(self, data):
            return types.SimpleNamespace(total_pages=0, pages=[])

    monkeypatch.setattr("liteparse.LiteParse", FakeLiteParse)
    with pytest.raises(IngestFailure):
        await LiteParseParser().parse(b"x", "d.pdf")


async def test_liteparse_parses_large_pdf_in_bounded_page_batches(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeLiteParse:
        def __init__(self, *a, **kwargs) -> None:
            self.target_pages = str(kwargs["target_pages"])
            calls.append(kwargs)

        def parse(self, data):
            if self.target_pages == "1":
                start = end = 1
            else:
                start, end = map(int, self.target_pages.split("-"))
            end = min(end, 1_201)
            return types.SimpleNamespace(
                total_pages=1_201,
                pages=[
                    types.SimpleNamespace(page_num=page, markdown=f"page {page}")
                    for page in range(start, end + 1)
                ],
            )

    monkeypatch.setattr("liteparse.LiteParse", FakeLiteParse)
    blocks = await LiteParseParser(batch_pages=500, max_pages=5_000).parse(
        b"pdf", "large.pdf"
    )

    assert [call["target_pages"] for call in calls] == [
        "1", "2-500", "501-1000", "1001-1201"
    ]
    assert all(call["max_pages"] == 5_000 for call in calls)
    assert len(blocks) == 1_201
    assert [blocks[0].page, blocks[-1].page] == [1, 1_201]


async def test_liteparse_fails_explicitly_above_ragz_max_pages(monkeypatch) -> None:
    calls = 0

    class FakeLiteParse:
        def __init__(self, *a, **kwargs) -> None:
            self.target_pages = kwargs["target_pages"]

        def parse(self, data):
            nonlocal calls
            calls += 1
            return types.SimpleNamespace(
                total_pages=1_201,
                pages=[types.SimpleNamespace(page_num=1, markdown="page 1")],
            )

    monkeypatch.setattr("liteparse.LiteParse", FakeLiteParse)
    with pytest.raises(LiteParsePageLimitExceeded, match="1,201.*1,000"):
        await LiteParseParser(batch_pages=500, max_pages=1_000).parse(
            b"pdf", "too-large.pdf"
        )
    assert calls == 1


async def test_liteparse_missing_page_in_batch_fails_closed(monkeypatch) -> None:
    class FakeLiteParse:
        def __init__(self, *a, **kwargs) -> None:
            self.target_pages = kwargs["target_pages"]

        def parse(self, data):
            pages = (
                [types.SimpleNamespace(page_num=1, markdown="one")]
                if self.target_pages == "1"
                else [types.SimpleNamespace(page_num=3, markdown="three")]
            )
            return types.SimpleNamespace(total_pages=3, pages=pages)

    monkeypatch.setattr("liteparse.LiteParse", FakeLiteParse)
    with pytest.raises(IngestFailure, match="incomplete page range.*2"):
        await LiteParseParser(batch_pages=3, max_pages=100).parse(b"pdf", "broken.pdf")


async def test_parse_document_liteparse_when_selected(session, settings, monkeypatch) -> None:
    await set_app_setting(session, "document_parser", "liteparse")

    called = {}

    async def _fake_parse(self, data, filename):
        called["hit"] = True
        return [PageBlock(page=4, text="lite", kind="text")]

    monkeypatch.setattr(LiteParseParser, "parse", _fake_parse)
    blocks = await parse_document(session, settings, data=b"x", filename="a.pdf")
    assert called.get("hit") is True
    assert [(b.page, b.text) for b in blocks] == [(4, "lite")]


async def test_liteparse_scanned_pdf_falls_back_to_docling_ocr(
    session, settings, monkeypatch
) -> None:
    await set_app_setting(session, "document_parser", "liteparse")

    async def _empty_scan(self, data, filename):
        raise IngestFailure("liteparse produced no extractable text")

    called = {}

    def _fake_parse_bytes(data, filename, *, ocr_enabled, ocr_min_chars_per_page):
        called["ocr_enabled"] = ocr_enabled
        return [PageBlock(page=9, text="ocr fallback", kind="text")]

    monkeypatch.setattr(LiteParseParser, "parse", _empty_scan)
    monkeypatch.setattr("ragz.modules.documents.parsers.parse_bytes", _fake_parse_bytes)

    blocks = await parse_document(
        session, settings, data=b"%PDF-1.7 image-only", filename="scan.pdf"
    )

    assert called["ocr_enabled"] is True
    assert blocks == [PageBlock(page=9, text="ocr fallback", kind="text")]


async def test_liteparse_page_limit_does_not_trigger_docling_fallback(
    session, settings, monkeypatch
) -> None:
    await set_app_setting(session, "document_parser", "liteparse")

    async def _over_limit(self, data, filename):
        raise LiteParsePageLimitExceeded("PDF has 1,001 pages; configured limit is 1,000")

    def _must_not_fallback(*args, **kwargs):
        raise AssertionError("page-limit failures must not rerun the PDF through Docling")

    monkeypatch.setattr(LiteParseParser, "parse", _over_limit)
    monkeypatch.setattr("ragz.modules.documents.parsers.parse_bytes", _must_not_fallback)

    with pytest.raises(LiteParsePageLimitExceeded):
        await parse_document(
            session, settings, data=b"%PDF-1.7 too many pages", filename="large.pdf"
        )


async def test_parse_document_llamaparse_without_key_raises(session, settings) -> None:
    await set_app_setting(session, "document_parser", "llamaparse")
    with pytest.raises(IngestFailure):
        await parse_document(session, settings, data=b"x", filename="a.pdf")


async def test_anydoc_parses_office_bytes_to_blocks():
    p = AnydocParser()
    blocks = await p.parse(b"name,age\nAlice,30\nBob,25\n", "data.csv")
    assert blocks and all(b.page == 1 for b in blocks)
    assert any("Alice" in b.text for b in blocks)


async def test_parse_document_anydoc_selected_uses_anydoc(session, settings):
    await set_app_setting(session, "document_parser", "anydoc")
    blocks = await parse_document(session, settings, data=b"a,b\n1,2\n", filename="t.csv")
    assert blocks and all(b.page == 1 for b in blocks)


async def test_anydoc_scanned_pdf_falls_back_to_docling_ocr(session, settings, monkeypatch):
    await set_app_setting(session, "document_parser", "anydoc")

    def _raise(*a, **k):
        # Real, directly-constructible ConvertError subclass (see Task 3
        # report for constructibility notes) -- still caught by the base
        # `except anydoc.ConvertError` in parse_document.
        raise anydoc.UnsupportedError("image-only pdf")  # variant: Unsupported/Malformed

    monkeypatch.setattr(anydoc, "to_markdown_bytes", _raise)

    called = {}
    def _fake_parse_bytes(data, filename, *, ocr_enabled, ocr_min_chars_per_page):
        called["ocr_enabled"] = ocr_enabled
        from ragz.modules.documents.pipeline import PageBlock
        return [PageBlock(page=7, text="ocr text", kind="text")]

    monkeypatch.setattr("ragz.modules.documents.parsers.parse_bytes", _fake_parse_bytes)
    blocks = await parse_document(session, settings, data=b"%PDF-1.7 fake", filename="scan.pdf")
    assert called["ocr_enabled"] is True          # Docling OCR path was taken
    assert [(b.page, b.text) for b in blocks] == [(7, "ocr text")]   # the fallback's blocks


async def test_anydoc_non_pdf_failure_raises_ingest_failure(session, settings, monkeypatch):
    await set_app_setting(session, "document_parser", "anydoc")
    def _raise(*a, **k):
        raise anydoc.UnsupportedError("unsupported")
    monkeypatch.setattr(anydoc, "to_markdown_bytes", _raise)
    with pytest.raises(IngestFailure):
        await parse_document(session, settings, data=b"junk", filename="mystery.xyz")


async def test_anydoc_default_routes_txt_to_docling(session, settings):
    # Neither local fast parser accepts plain text; an unset parser must still
    # ingest .txt through the dedicated local text path.
    blocks = await parse_document(
        session, settings, data=b"first para\n\nsecond para\n", filename="notes.txt"
    )
    assert [b.text for b in blocks] == ["first para", "second para"]
    assert all(b.kind == "text" for b in blocks)
