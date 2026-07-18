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


@dataclass(frozen=True)
class Chunk:
    text: str
    page: int
    chunk_index: int


def parse_bytes(data: bytes, filename: str) -> list[PageBlock]:
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

    # Deferred heavy imports: keep docling out of the API process entirely.
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import (  # type: ignore[attr-defined]
        DocItemLabel,
        TableItem,
        TextItem,
    )

    heading_labels = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        try:
            result = DocumentConverter().convert(tmp_path, raises_on_error=True)
        except Exception as exc:  # docling raises assorted types for bad input
            raise IngestFailure(f"unsupported or unparsable document: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    blocks: list[PageBlock] = []
    for item, _level in result.document.iterate_items():
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else 1
        if isinstance(item, TableItem):
            text, kind = item.export_to_markdown(result.document), "table"
        elif isinstance(item, TextItem):
            text = item.text
            kind = "heading" if item.label in heading_labels else "text"
        else:
            continue
        if text.strip():
            blocks.append(PageBlock(page=page, text=text.strip(), kind=kind))
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


def chunk_blocks(
    blocks: list[PageBlock], *, target_chars: int = 2000, overlap_ratio: float = 0.15
) -> list[Chunk]:
    """Heading-aware chunking: ~512 tokens ≈ 2000 chars, 15% overlap between
    consecutive chunks, tables emitted whole as their own chunk."""
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_page: int | None = None
    overlap_chars = int(target_chars * overlap_ratio)

    def flush(carry_overlap: bool) -> None:
        nonlocal buf, buf_page
        if not buf:
            return
        text = "\n\n".join(buf)
        chunks.append(Chunk(text=text, page=buf_page or 1, chunk_index=len(chunks)))
        if carry_overlap and overlap_chars > 0:
            tail = text[-overlap_chars:]
            cut = tail.find(" ")  # start the overlap on a word boundary
            buf = [tail[cut + 1 :] if 0 <= cut < len(tail) - 1 else tail]
            # buf_page intentionally kept: the overlap belongs to the same region
        else:
            buf, buf_page = [], None

    for block in blocks:
        if block.kind == "table":
            flush(carry_overlap=False)
            chunks.append(Chunk(text=block.text, page=block.page, chunk_index=len(chunks)))
            continue
        if block.kind == "heading" and buf and len("\n\n".join(buf)) >= target_chars // 2:
            flush(carry_overlap=False)
        for piece in _split_text(block.text, target_chars):
            # Flush BEFORE appending a piece that would overflow the target, so a
            # chunk never exceeds ~target + overlap chars (pieces are <= target).
            if buf and len("\n\n".join(buf)) + len(piece) > target_chars:
                flush(carry_overlap=True)
            if buf_page is None:
                buf_page = block.page
            buf.append(piece)
    flush(carry_overlap=False)
    return chunks


async def embed_batch(
    texts: list[str], dense_embedder: DenseEmbedder
) -> tuple[list[list[float]], list[models.SparseVector]]:
    """Embed one batch dense + sparse (spec §3.2 stage 3)."""
    dense = await dense_embedder.embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, texts)
    return dense, sparse


async def upsert_points(
    *,
    org_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    mime: str,
    created_at: datetime,
    chunks: list[Chunk],
    dense: list[list[float]],
    sparse: list[models.SparseVector],
) -> None:
    """Upsert one batch of chunk points with the spec §2.2 payload. Constructs
    points, never filters (iron rule 1 — filters live in retrieval only)."""
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
                "acl_groups": [],  # reserved: Phase 2 ACLs need no schema migration
            },
        )
        for c, d, s in zip(chunks, dense, sparse, strict=True)
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
