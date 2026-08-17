"""Pluggable document parser seam. `document_parser` app_setting selects the
backend: `anydoc` (default, self-hosted, pure-Rust, with a scanned-PDF fallback
to Docling OCR), `docling` (self-hosted, byte-identical to the original direct
parse_bytes call), or `llamaparse` (LlamaIndex cloud via REST, no SDK). All
return list[PageBlock] so the chunk/embed pipeline downstream is unchanged."""

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

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

    async def parse(self, data: bytes, filename: str) -> list[PageBlock]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(
                base_url=_LLAMA_BASE, timeout=60.0, transport=self._transport,
                headers=headers,
            ) as client:
                up = await client.post(
                    "/upload", files={"file": (filename, data)}
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
                        raise IngestFailure("LlamaParse reported an error parsing the file")
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

    async def parse(self, data: bytes, filename: str) -> list[PageBlock]:
        import anydoc

        hint = _ANYDOC_FORMAT_HINTS.get(Path(filename).suffix.lower())

        def _convert() -> str:
            return anydoc.to_markdown_bytes(data, hint) if hint else anydoc.to_markdown_bytes(data)

        markdown = await asyncio.to_thread(_convert)
        blocks = _markdown_to_blocks(markdown)
        if not blocks:
            raise IngestFailure("anydoc produced no extractable text")
        return blocks


#: liteparse defaults to max_pages=1000 and truncates SILENTLY past it -- no
#: error, no warning, the document just ends. A 1518-page manual ingested as
#: its first 1000 pages, so every later section (for a reference manual, the
#: back-of-book function index) was absent from retrieval while the document
#: looked fully indexed. Ragz never wants a partial document: a truncated
#: corpus produces confidently wrong "not in the sources" answers, which is
#: worse than a slow ingest. Set far above any real document rather than
#: unbounded so a corrupt page count still terminates.
_MAX_PAGES = 10_000_000


class LiteParseParser:
    """run-llama liteparse (PDFium, self-hosted, offline): per-page markdown
    with a real page_num -> blocks. Fast like anydoc, page-accurate like
    docling. OCR off by default (text PDFs); flip ocr_enabled for scanned."""

    async def parse(self, data: bytes, filename: str) -> list[PageBlock]:
        from liteparse import LiteParse

        def _convert() -> list[PageBlock]:
            res = LiteParse(
                ocr_enabled=False, quiet=True, output_format="markdown",
                max_pages=_MAX_PAGES,
            ).parse(data)
            blocks: list[PageBlock] = []
            for page in res.pages:
                blocks.extend(_markdown_to_blocks(page.markdown or "", page=page.page_num))
            return blocks

        blocks = await asyncio.to_thread(_convert)
        if not blocks:
            raise IngestFailure("liteparse produced no extractable text")
        return blocks


async def parse_document(
    session: AsyncSession, settings: Settings, *, data: bytes, filename: str
) -> list[PageBlock]:
    parser = await get_app_setting(session, "document_parser")
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
    if parser == "liteparse":
        return await LiteParseParser().parse(data, filename)
    # anydoc is the default (parser == "anydoc", or unset/unknown).
    import anydoc
    is_pdf = Path(filename).suffix.lower() == ".pdf"
    # anydoc supports no plain-text format; Docling's parse_bytes has a
    # dedicated UTF-8 .txt path (no OCR needed). Route .txt there directly
    # rather than calling anydoc just to catch its UnsupportedError.
    if Path(filename).suffix.lower() == ".txt":
        return await asyncio.to_thread(
            parse_bytes, data, filename,
            ocr_enabled=settings.ocr_enabled,
            ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
        )
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
