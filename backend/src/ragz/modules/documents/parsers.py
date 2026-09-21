"""Pluggable document parser seam. `document_parser` app_setting selects the
backend: `liteparse` (default, self-hosted PDFium), `anydoc` (self-hosted,
pure-Rust, with a scanned-PDF fallback to Docling OCR), `docling` (self-hosted),
or `llamaparse` (LlamaIndex cloud via REST, no SDK). All return
list[PageBlock] so the chunk/embed pipeline downstream is unchanged."""

import asyncio
import re
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.config import Settings
from ragz.core.errors import NotFoundError
from ragz.modules.documents.pipeline import IngestFailure, PageBlock, parse_bytes
from ragz.modules.secrets import service as secrets_service

if TYPE_CHECKING:
    import anydoc

_log = structlog.get_logger("ragz.documents.parsers")

_LLAMA_BASE = "https://api.cloud.llamaindex.ai/api/v1/parsing"
_LLAMA_POLL_INTERVAL = 3.0
_LLAMA_MAX_POLLS = 100  # ~5 min at 3s

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")

DocumentInput = bytes | str | Path

# Signature-less formats anydoc cannot sniff from bytes; pass the extension.
_ANYDOC_FORMAT_HINTS: dict[str, "anydoc.Format"] = {".csv": "csv"}


def _markdown_to_blocks(markdown: str, page: int = 1) -> list[PageBlock]:
    """Map flat GFM Markdown to PageBlocks. anydoc has no page boundaries so it
    defaults page=1 (documented trade-off); liteparse passes the real page_num.
    Headings carry `level` so the existing chunker derives section trails, and
    GFM pipe tables are emitted whole as one `table` block (the chunker emits a
    table as its own chunk). Everything else is text, split on blank lines."""
    lines = markdown.splitlines()
    blocks: list[PageBlock] = []
    para: list[str] = []

    def flush_para() -> None:
        text = "\n".join(para).strip()
        if text:
            blocks.append(PageBlock(page=page, text=text, kind="text"))
        para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _ATX_HEADING.match(line)
        if heading:
            flush_para()
            blocks.append(
                PageBlock(page=page, text=heading.group(2).strip(),
                          kind="heading", level=len(heading.group(1)))
            )
            i += 1
            continue
        # GFM table: a pipe row immediately followed by a separator row.
        if (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEP.match(lines[i + 1])
        ):
            flush_para()
            table = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table.append(lines[i])
                i += 1
            blocks.append(PageBlock(page=page, text="\n".join(table).strip(), kind="table"))
            continue
        if line.strip():
            para.append(line)
        else:
            flush_para()
        i += 1
    flush_para()
    return blocks


class LlamaParseParser:
    """SDK-free LlamaParse client: upload -> poll job -> fetch JSON result,
    mapping each page to a PageBlock. Any failure raises IngestFailure so
    run_parse fails the document with a clear, user-visible message."""

    def __init__(
        self, *, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._transport = transport

    async def parse(self, data: DocumentInput, filename: str) -> list[PageBlock]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        source = (
            nullcontext(BytesIO(data))
            if isinstance(data, bytes)
            else Path(data).open("rb")
        )
        try:
            with source as upload:
                async with httpx.AsyncClient(
                    base_url=_LLAMA_BASE,
                    timeout=60.0,
                    transport=self._transport,
                    headers=headers,
                ) as client:
                    up = await client.post(
                        "/upload", files={"file": (filename, upload)}
                    )
                    up.raise_for_status()
                    job_id = str(up.json()["id"])
                    for _ in range(_LLAMA_MAX_POLLS):
                        st = await client.get(f"/job/{job_id}")
                        st.raise_for_status()
                        status = st.json().get("status")
                        if status == "SUCCESS":
                            break
                        if status == "ERROR":
                            raise IngestFailure(
                                "LlamaParse reported an error parsing the file"
                            )
                        await asyncio.sleep(_LLAMA_POLL_INTERVAL)
                    else:
                        raise IngestFailure("LlamaParse timed out")
                    res = await client.get(f"/job/{job_id}/result/json")
                    res.raise_for_status()
                    pages = res.json().get("pages", [])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise IngestFailure("LlamaParse request failed") from exc
        blocks = [
            PageBlock(page=int(pg.get("page", i + 1)),
                      text=(pg.get("md") or pg.get("text") or "").strip(),
                      kind="text")
            for i, pg in enumerate(pages)
        ]
        blocks = [b for b in blocks if b.text]
        if not blocks:
            raise IngestFailure("LlamaParse returned no extractable text")
        return blocks


class AnydocParser:
    """Firecrawl anydoc (pure-Rust, <5ms): bytes -> flat GFM Markdown -> blocks
    (page=1, headings/tables preserved). No OCR and no page boundaries; the
    scanned-PDF fallback lives in parse_document, not here."""

    async def parse(self, data: DocumentInput, filename: str) -> list[PageBlock]:
        import anydoc

        hint = _ANYDOC_FORMAT_HINTS.get(Path(filename).suffix.lower())

        def _convert() -> str:
            if isinstance(data, bytes):
                return (
                    anydoc.to_markdown_bytes(data, hint)
                    if hint
                    else anydoc.to_markdown_bytes(data)
                )
            return anydoc.to_markdown(str(Path(data)))

        markdown = await asyncio.to_thread(_convert)
        blocks = _markdown_to_blocks(markdown)
        if not blocks:
            raise IngestFailure("anydoc produced no extractable text")
        return blocks


class LiteParsePageLimitExceeded(IngestFailure):
    """The source is larger than Ragz is configured to ingest completely."""


class LiteParseParser:
    """run-llama liteparse (PDFium, self-hosted, offline): per-page markdown
    with real page numbers -> blocks. LiteParse silently caps an unconfigured
    parse at 1,000 pages, so Ragz first probes the source total and then parses
    bounded ranges. Every range stays under one Ragz document id and original
    citation pages are preserved."""

    def __init__(self, *, batch_pages: int = 250, max_pages: int = 10_000) -> None:
        if batch_pages <= 0 or max_pages <= 0:
            raise ValueError("LiteParse page limits must be positive")
        self._batch_pages = batch_pages
        self._max_pages = max_pages

    async def parse(self, data: DocumentInput, filename: str) -> list[PageBlock]:
        from liteparse import LiteParse

        def _convert() -> list[PageBlock]:
            blocks: list[PageBlock] = []

            def parse_range(target_pages: str) -> tuple[int, list[Any]]:
                result = LiteParse(
                    ocr_enabled=False,
                    quiet=True,
                    output_format="markdown",
                    max_pages=self._max_pages,
                    target_pages=target_pages,
                ).parse(data)
                total_pages = int(getattr(result, "total_pages", 0) or 0)
                pages = sorted(result.pages, key=lambda page: int(page.page_num))
                return total_pages, pages

            def consume_range(pages: list[Any], *, start: int, end: int) -> None:
                actual = [int(page.page_num) for page in pages]
                expected = list(range(start, end + 1))
                if actual != expected:
                    missing = sorted(set(expected) - set(actual))
                    unexpected = sorted(set(actual) - set(expected))
                    details = []
                    if missing:
                        details.append(f"missing pages: {', '.join(map(str, missing[:20]))}")
                    if unexpected:
                        details.append(
                            f"unexpected pages: {', '.join(map(str, unexpected[:20]))}"
                        )
                    raise IngestFailure(
                        f"liteparse returned incomplete page range {start}-{end}; "
                        + "; ".join(details)
                    )
                for page in pages:
                    blocks.extend(
                        _markdown_to_blocks(page.markdown or "", page=int(page.page_num))
                    )

            source_total, probe_pages = parse_range("1")
            if source_total <= 0:
                return []
            if source_total > self._max_pages:
                raise LiteParsePageLimitExceeded(
                    f"PDF has {source_total:,} pages; configured Ragz LiteParse "
                    f"limit is {self._max_pages:,} pages"
                )
            consume_range(probe_pages, start=1, end=1)

            first_end = min(self._batch_pages, source_total)
            if first_end >= 2:
                total, pages = parse_range(f"2-{first_end}")
                if total != source_total:
                    raise IngestFailure("liteparse source page count changed between batches")
                consume_range(pages, start=2, end=first_end)
            for start in range(self._batch_pages + 1, source_total + 1, self._batch_pages):
                end = min(start + self._batch_pages - 1, source_total)
                total, pages = parse_range(f"{start}-{end}")
                if total != source_total:
                    raise IngestFailure("liteparse source page count changed between batches")
                consume_range(pages, start=start, end=end)
            return blocks

        blocks = await asyncio.to_thread(_convert)
        if not blocks:
            raise IngestFailure("liteparse produced no extractable text")
        return blocks


async def parse_document(
    session: AsyncSession, settings: Settings, *, data: DocumentInput, filename: str
) -> list[PageBlock]:
    parser = await get_app_setting(session, "document_parser")
    # The fast local parsers do not accept plain text. Keep this exact parser
    # for .txt unless the operator explicitly selected cloud LlamaParse.
    if Path(filename).suffix.lower() == ".txt" and parser != "llamaparse":
        return await asyncio.to_thread(
            parse_bytes,
            data,
            filename,
            ocr_enabled=settings.ocr_enabled,
            ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
        )
    if parser == "llamaparse":
        try:
            key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
                session, name="llamaparse_api_key", settings=settings
            )
        except NotFoundError as exc:
            raise IngestFailure(
                "LlamaParse selected but no API key is configured"
            ) from exc
        return await LlamaParseParser(api_key=key).parse(data, filename)
    if parser == "docling":
        return await asyncio.to_thread(
            parse_bytes, data, filename,
            ocr_enabled=settings.ocr_enabled,
            ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
        )
    if parser in (None, "liteparse") and Path(filename).suffix.lower() == ".pdf":
        try:
            return await LiteParseParser(
                batch_pages=settings.liteparse_batch_pages,
                max_pages=settings.liteparse_max_pages,
            ).parse(data, filename)
        except LiteParsePageLimitExceeded:
            # A configured resource boundary must not be bypassed by retrying
            # the entire oversized source through Docling.
            raise
        except IngestFailure:
            if Path(filename).suffix.lower() == ".pdf" and settings.ocr_enabled:
                _log.info("documents.liteparse_pdf_fallback_to_docling", filename=filename)
                return await asyncio.to_thread(
                    parse_bytes,
                    data,
                    filename,
                    ocr_enabled=True,
                    ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
                )
            raise
    # Explicit anydoc (or a legacy unknown setting) reaches the flat-markdown
    # path. The unset default is LiteParse, matching the settings API and UI.
    import anydoc
    is_pdf = Path(filename).suffix.lower() == ".pdf"
    try:
        return await AnydocParser().parse(data, filename)
    except anydoc.ConvertError as exc:
        if is_pdf:
            _log.info("documents.anydoc_pdf_fallback_to_docling", filename=filename)
            return await asyncio.to_thread(
                parse_bytes, data, filename,
                ocr_enabled=True,
                ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
            )
        raise IngestFailure("unsupported or unreadable document") from exc
