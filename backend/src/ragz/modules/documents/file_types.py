"""Server-derived document types and browser preview policy."""

import codecs
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from ragz.core.errors import UnsupportedMediaType

_SNIFF_BYTES = 8192
_ACTIVE_EXTENSIONS = frozenset({".html", ".htm", ".svg"})
_MAX_OOXML_ENTRIES = 100_000
_MAX_OOXML_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


class PreviewKind(StrEnum):
    FRAME = "frame"
    IMAGE = "image"
    TEXT = "text"
    DOWNLOAD = "download"


@dataclass(frozen=True, slots=True)
class DocumentFileType:
    mime: str
    preview: PreviewKind


_TEXT_TYPES = {
    ".txt": DocumentFileType("text/plain", PreviewKind.TEXT),
    ".md": DocumentFileType("text/markdown", PreviewKind.TEXT),
    ".csv": DocumentFileType("text/csv", PreviewKind.TEXT),
    ".html": DocumentFileType("text/html", PreviewKind.DOWNLOAD),
    ".htm": DocumentFileType("text/html", PreviewKind.DOWNLOAD),
}

_OOXML_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_IMAGE_TYPES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".gif": ("image/gif", (b"GIF87a", b"GIF89a")),
    ".bmp": ("image/bmp", (b"BM",)),
    ".tif": ("image/tiff", (b"II*\x00", b"MM\x00*")),
    ".tiff": ("image/tiff", (b"II*\x00", b"MM\x00*")),
}


def _read_head(stream: BinaryIO) -> bytes:
    position = stream.tell()
    try:
        stream.seek(0)
        return stream.read(_SNIFF_BYTES)
    finally:
        stream.seek(position)


def _valid_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _valid_full_text(stream: BinaryIO) -> bool:
    position = stream.tell()
    try:
        stream.seek(0)
        decoder = codecs.getincrementaldecoder("utf-8")()
        while chunk := stream.read(1024 * 1024):
            if b"\x00" in chunk:
                return False
            try:
                decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                return False
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            return False
        return True
    finally:
        stream.seek(position)


def _has_pdf_eof(stream: BinaryIO) -> bool:
    position = stream.tell()
    try:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - 4096))
        return b"%%EOF" in stream.read()
    finally:
        stream.seek(position)


def _valid_ooxml(stream: BinaryIO, suffix: str) -> bool:
    required = {
        ".docx": "word/document.xml",
        ".xlsx": "xl/workbook.xml",
        ".pptx": "ppt/presentation.xml",
    }[suffix]
    position = stream.tell()
    try:
        stream.seek(0)
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_OOXML_ENTRIES:
                return False
            if sum(entry.file_size for entry in entries) > _MAX_OOXML_UNCOMPRESSED_BYTES:
                return False
            names = {entry.filename for entry in entries}
            return "[Content_Types].xml" in names and required in names
    except (BadZipFile, OSError):
        return False
    finally:
        stream.seek(position)


def _classify(filename: str, stream: BinaryIO) -> DocumentFileType:
    suffix = Path(filename).suffix.lower()
    head = _read_head(stream)

    if suffix == ".pdf" and head.startswith(b"%PDF-"):
        return DocumentFileType("application/pdf", PreviewKind.FRAME)

    if suffix in _OOXML_TYPES and head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return DocumentFileType(_OOXML_TYPES[suffix], PreviewKind.DOWNLOAD)

    if suffix == ".webp" and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return DocumentFileType("image/webp", PreviewKind.IMAGE)

    image = _IMAGE_TYPES.get(suffix)
    if image is not None and head.startswith(image[1]):
        return DocumentFileType(image[0], PreviewKind.IMAGE)

    if suffix == ".svg" and _valid_text(head) and b"<svg" in head.lower():
        return DocumentFileType("image/svg+xml", PreviewKind.DOWNLOAD)

    text_type = _TEXT_TYPES.get(suffix)
    if text_type is not None and _valid_text(head):
        return text_type

    raise UnsupportedMediaType("unsupported file type or content does not match its extension")


def classify_upload(filename: str, stream: BinaryIO) -> DocumentFileType:
    """Validate a new upload and return its canonical server-derived type."""

    file_type = _classify(filename, stream)
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and not _has_pdf_eof(stream):
        raise UnsupportedMediaType("PDF is truncated or missing its end marker")
    if suffix in _OOXML_TYPES and not _valid_ooxml(stream, suffix):
        raise UnsupportedMediaType("invalid Office Open XML container")
    if suffix in _TEXT_TYPES and not _valid_full_text(stream):
        raise UnsupportedMediaType("text file must be valid UTF-8 without NUL bytes")
    return file_type


def classify_stored_file(filename: str, stream: BinaryIO) -> DocumentFileType:
    """Classify legacy bytes without making old files unavailable."""

    try:
        file_type = _classify(filename, stream)
    except UnsupportedMediaType:
        return DocumentFileType("application/octet-stream", PreviewKind.DOWNLOAD)
    if Path(filename).suffix.lower() in _ACTIVE_EXTENSIONS:
        return DocumentFileType("application/octet-stream", PreviewKind.DOWNLOAD)
    return file_type
