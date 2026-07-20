"""Ephemeral chat-attachment text extraction. Reuses the SAME Docling
primitive the permanent-document pipeline uses (`parse_bytes`) — no
separate OCR code path exists or is needed. Docling's default image
pipeline (InputFormat.IMAGE) already runs OCR (do_ocr=True by default),
so a photo/screenshot attachment extracts text through the identical call
as a text document; no branching on `kind` happens here."""

from raghub.modules.documents.pipeline import parse_bytes


def extract_text(data: bytes, filename: str) -> str:
    """Best-effort text extraction for a chat attachment (document or
    image). Returns "" on any parse failure rather than raising — a failed
    extraction degrades to "no inline/retrieval content available" for this
    attachment, never blocks the chat."""
    try:
        blocks = parse_bytes(data, filename)
    except Exception:
        return ""
    return "\n\n".join(b.text for b in blocks)
