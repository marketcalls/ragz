"""Ingestion stage functions: parse → chunk → embed → upsert (spec §3.2).

Pure-ish and unit-testable: no sessions, no Celery. Orchestration and job-status
writes live in modules/documents/ingest.py; Celery wrappers in worker/tasks.py.
"""

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from qdrant_client import models

from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import DenseEmbedder, embed_sparse

# Deterministic point ids: retried upserts overwrite instead of duplicating.
_CHUNK_NAMESPACE = UUID("6c7d9a52-3e1f-4b8a-9c0d-2f5e8b1a7d43")


class IngestFailure(Exception):
    """Terminal, non-retryable ingestion failure (bad input, not infrastructure)."""


@dataclass(frozen=True)
class PageBlock:
    page: int
    text: str
    kind: str  # "text" | "heading" | "table"
    level: int | None = None  # heading depth; None for non-headings


@dataclass(frozen=True)
class Chunk:
    text: str
    page: int
    chunk_index: int
    section: str | None = None  # "H1 > H2 > H3" heading trail (CHAT-4)


def needs_ocr(blocks: list["PageBlock"], *, min_chars_per_page: int) -> bool:
    """DOC-3 auto-detection: a PDF is 'scanned' when average extracted text per
    page falls below the threshold. Zero blocks is trivially below it."""
    if not blocks:
        return True
    page_count = max(b.page for b in blocks)
    total_chars = sum(len(b.text) for b in blocks)
    return total_chars / max(page_count, 1) < min_chars_per_page


def _convert_blocks(tmp_path: Path, suffix: str, *, ocr: bool) -> list["PageBlock"]:
    """One Docling conversion → PageBlocks. ocr=False keeps today's default
    converter; ocr=True forces full-page EasyOCR (scanned-PDF rescue pass)."""
    # Deferred heavy imports: keep docling out of the API process entirely.
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import (  # type: ignore[attr-defined]
        SectionHeaderItem,
        TableItem,
        TextItem,
        TitleItem,
    )

    if ocr:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
        from docling.document_converter import PdfFormatOption

        opts = PdfPipelineOptions(do_ocr=True)
        opts.ocr_options = EasyOcrOptions(force_full_page_ocr=True)
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    else:
        converter = DocumentConverter()

    try:
        result = converter.convert(tmp_path, raises_on_error=True)
    except Exception as exc:  # docling raises assorted types for bad input
        raise IngestFailure(f"unsupported or unparsable document: {exc}") from exc

    blocks: list[PageBlock] = []
    for item, _level in result.document.iterate_items():
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else 1
        if isinstance(item, TableItem):
            text, kind, level = item.export_to_markdown(result.document), "table", None
        elif isinstance(item, TitleItem):
            text, kind, level = item.text, "heading", 0
        elif isinstance(item, SectionHeaderItem):
            text, kind, level = item.text, "heading", int(item.level)
        elif isinstance(item, TextItem):
            text, kind, level = item.text, "text", None
        else:
            continue
        if text.strip():
            blocks.append(PageBlock(page=page, text=text.strip(), kind=kind, level=level))
    return blocks


def parse_bytes(
    data: bytes,
    filename: str,
    *,
    ocr_enabled: bool = True,
    ocr_min_chars_per_page: int = 200,
) -> list[PageBlock]:
    """Docling parse to page-aware blocks. Sync/CPU — call via asyncio.to_thread."""
    if not data:
        raise IngestFailure("file is empty")
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":  # docling has no plain-text input format
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestFailure("text file is not valid UTF-8") from exc
        txt_blocks = [
            PageBlock(page=1, text=p.strip(), kind="text")
            for p in text.split("\n\n") if p.strip()
        ]
        if not txt_blocks:
            raise IngestFailure("document contains no extractable text")
        return txt_blocks

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        blocks = _convert_blocks(tmp_path, suffix, ocr=False)
        if (
            suffix == ".pdf"
            and ocr_enabled
            and needs_ocr(blocks, min_chars_per_page=ocr_min_chars_per_page)
        ):
            blocks = _convert_blocks(tmp_path, suffix, ocr=True)
    finally:
        tmp_path.unlink(missing_ok=True)
    if not blocks:
        raise IngestFailure("document contains no extractable text")
    return blocks


def _split_text(text: str, limit: int) -> list[str]:
    """Split into pieces of at most `limit` chars on whitespace boundaries."""
    words = text.split()
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for w in words:
        if size + len(w) + 1 > limit and buf:
            pieces.append(" ".join(buf))
            buf, size = [], 0
        buf.append(w)
        size += len(w) + 1
    if buf:
        pieces.append(" ".join(buf))
    return pieces


_SECTION_LEVELS = 3
_SECTION_PART_CHARS = 80


def _trail_push(trail: list[tuple[int, str]], level: int, text: str) -> None:
    """A heading at level N closes every open heading at level >= N."""
    while trail and trail[-1][0] >= level:
        trail.pop()
    trail.append((level, text[:_SECTION_PART_CHARS]))


def _trail_section(trail: list[tuple[int, str]]) -> str | None:
    if not trail:
        return None
    return " > ".join(t for _, t in trail[-_SECTION_LEVELS:])


def chunk_blocks(
    blocks: list[PageBlock], *, target_chars: int = 2000, overlap_ratio: float = 0.15
) -> list[Chunk]:
    """Heading-aware chunking: ~512 tokens ≈ 2000 chars, 15% overlap between
    consecutive chunks, tables emitted whole as their own chunk."""
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_page: int | None = None
    buf_section: str | None = None
    trail: list[tuple[int, str]] = []
    overlap_chars = int(target_chars * overlap_ratio)

    def flush(carry_overlap: bool) -> None:
        nonlocal buf, buf_page, buf_section
        if not buf:
            return
        text = "\n\n".join(buf)
        chunks.append(
            Chunk(text=text, page=buf_page or 1, chunk_index=len(chunks), section=buf_section)
        )
        if carry_overlap and overlap_chars > 0:
            tail = text[-overlap_chars:]
            cut = tail.find(" ")  # start the overlap on a word boundary
            buf = [tail[cut + 1 :] if 0 <= cut < len(tail) - 1 else tail]
            # buf_page/buf_section intentionally kept: the overlap belongs to the same region
        else:
            buf, buf_page, buf_section = [], None, None

    for block in blocks:
        if block.kind == "table":
            flush(carry_overlap=False)
            chunks.append(
                Chunk(
                    text=block.text,
                    page=block.page,
                    chunk_index=len(chunks),
                    section=_trail_section(trail),
                )
            )
            continue
        if block.kind == "heading":
            _trail_push(trail, block.level if block.level is not None else 1, block.text)
            # A heading is a new section: never merge its text into the prior
            # buffer, so each chunk's single `section` trail stays accurate.
            if buf:
                flush(carry_overlap=False)
        for piece in _split_text(block.text, target_chars):
            # Flush BEFORE appending a piece that would overflow the target, so a
            # chunk never exceeds ~target + overlap chars (pieces are <= target).
            if buf and len("\n\n".join(buf)) + len(piece) > target_chars:
                flush(carry_overlap=True)
            if buf_page is None:
                buf_page = block.page
                buf_section = _trail_section(trail)
            buf.append(piece)
    flush(carry_overlap=False)
    return chunks


async def embed_batch(
    texts: list[str], dense_embedder: DenseEmbedder, *, sparse_texts: list[str] | None = None
) -> tuple[list[list[float]], list[models.SparseVector]]:
    """Embed one batch dense + sparse (spec §3.2 stage 3). `sparse_texts`
    (Plan K §4) lets keyword-augmented text feed ONLY the sparse/BM25 side —
    dense embeddings stay semantically anchored to the chunk's real content;
    keywords are a lexical-match aid, not a meaning shift. Defaults to
    `texts` so every pre-K caller is byte-identical."""
    dense = await dense_embedder.embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, sparse_texts or texts)
    return dense, sparse


async def upsert_points(
    *,
    org_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    mime: str,
    created_at: datetime,
    acl_group_ids: list[str],
    chunks: list[Chunk],
    dense: list[list[float]],
    sparse: list[models.SparseVector],
    version: int,
    meta: dict[str, str] | None,
    is_current: bool = False,
    summaries: list[str | None] | None = None,
) -> None:
    """Upsert one batch of chunk points with the spec §2.2 payload. Constructs
    points, never filters (iron rule 1 — filters live in retrieval only).

    Plan H: every point is stamped with its document's `version` and `section`
    (heading trail). `is_current` defaults False — points are invisible to
    current_only retrieval until promotion (Task 6) flips them via
    `retrieval.service.update_document_current`. `meta` (DOC-6, required — no
    default, so every caller states its posture) is the document's metadata
    field values, mirrored verbatim under the payload's nested `meta` key;
    `None` becomes `{}` so every point carries the key (never a KeyError on
    the read side). `summaries` (Plan K §4) is an optional per-chunk LLM
    summary, aligned by index with `chunks`; omitted or a `None` entry keeps
    the payload's `summary` key `None` so pre-K points and non-enriched
    ingests are unaffected."""
    summaries = summaries or [None] * len(chunks)
    points = [
        models.PointStruct(
            id=str(uuid5(_CHUNK_NAMESPACE, f"{document_id}:{c.chunk_index}")),
            vector={"dense": d, "sparse": s},
            payload={
                "tenant_id": str(org_id),
                "workspace_id": str(workspace_id),
                "document_id": str(document_id),
                "page": c.page,
                "chunk_index": c.chunk_index,
                "text": c.text,
                "doc_type": mime,
                "date": created_at.isoformat(),
                "acl_groups": sorted(acl_group_ids),
                "section": c.section,
                "version": version,
                "is_current": is_current,
                "meta": meta or {},
                "summary": summary,
            },
        )
        for c, d, s, summary in zip(chunks, dense, sparse, summaries, strict=True)
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)


# Distinct from _CHUNK_NAMESPACE (Plan K §4): hq points must never collide
# with or overwrite their parent chunk point.
_HQ_NAMESPACE = UUID("9f3c1a86-7b2e-4d5f-8e1a-3c6b9d0f2a71")


async def upsert_hq_points(
    *,
    org_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    mime: str,
    created_at: datetime,
    acl_group_ids: list[str],
    version: int,
    meta: dict[str, str] | None,
    is_current: bool,
    parent_chunks: list[Chunk],
    parent_summaries: list[str | None],
    hq_texts: list[list[str]],
    hq_dense: list[list[list[float]]],
    hq_sparse: list[list[models.SparseVector]],
) -> None:
    """Hypothetical-question points (spec §4): one per generated question, up
    to 3 per chunk. Payload is a FULL COPY of the parent chunk's payload
    (identical text/page/chunk_index/section/version/is_current/meta/
    acl_groups/summary) plus kind="hq" — an hq hit is immediately citable
    with the parent's real content, and shares the parent's chunk_ref
    (document_id, page, chunk_index) so retrieval can dedupe them (Task 5).
    The question TEXT only ever feeds the embedding vectors, never the
    payload — this function never constructs a Qdrant filter (iron rule 1);
    it is a plain point-write, same posture as upsert_points."""
    points: list[models.PointStruct] = []
    for chunk, summary, questions, dense_vecs, sparse_vecs in zip(
        parent_chunks, parent_summaries, hq_texts, hq_dense, hq_sparse, strict=True
    ):
        for i, (q_dense, q_sparse) in enumerate(zip(dense_vecs, sparse_vecs, strict=True)):
            points.append(
                models.PointStruct(
                    id=str(uuid5(_HQ_NAMESPACE, f"{document_id}:{chunk.chunk_index}:hq:{i}")),
                    vector={"dense": q_dense, "sparse": q_sparse},
                    payload={
                        "tenant_id": str(org_id),
                        "workspace_id": str(workspace_id),
                        "document_id": str(document_id),
                        "page": chunk.page,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                        "doc_type": mime,
                        "date": created_at.isoformat(),
                        "acl_groups": sorted(acl_group_ids),
                        "section": chunk.section,
                        "version": version,
                        "is_current": is_current,
                        "meta": meta or {},
                        "summary": summary,
                        "kind": "hq",
                    },
                )
            )
        del questions  # question text feeds embedding upstream of this function, not payload
    if points:
        await get_qdrant().upsert(COLLECTION, points=points, wait=True)
