from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from ragz.core.errors import UnsupportedMediaType
from ragz.modules.documents.file_types import (
    PreviewKind,
    classify_stored_file,
    classify_upload,
)


@pytest.mark.parametrize(
    ("filename", "body", "mime", "preview"),
    [
        ("manual.pdf", b"%PDF-1.7\n%%EOF\n", "application/pdf", PreviewKind.FRAME),
        ("notes.txt", b"plain <script>text</script>", "text/plain", PreviewKind.TEXT),
        ("notes.md", b"# Heading\n", "text/markdown", PreviewKind.TEXT),
        ("table.csv", b"name,value\na,1\n", "text/csv", PreviewKind.TEXT),
        (
            "page.html",
            b"<!doctype html><script>parent.pwned=1</script>",
            "text/html",
            PreviewKind.DOWNLOAD,
        ),
        (
            "vector.svg",
            b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>",
            "image/svg+xml",
            PreviewKind.DOWNLOAD,
        ),
    ],
)
def test_supported_uploads_use_server_derived_type_and_preview_policy(
    filename: str, body: bytes, mime: str, preview: PreviewKind
) -> None:
    stream = BytesIO(body)

    result = classify_upload(filename, stream)

    assert result.mime == mime
    assert result.preview is preview
    assert stream.tell() == 0


@pytest.mark.parametrize(
    ("filename", "body"),
    [
        ("fake.pdf", b"<!doctype html><script>parent.pwned=1</script>"),
        ("unknown.bin", b"arbitrary bytes"),
        ("broken.svg", b"not an svg"),
        ("nul.txt", b"text\x00binary"),
    ],
)
def test_unsupported_or_spoofed_uploads_are_rejected(filename: str, body: bytes) -> None:
    with pytest.raises(UnsupportedMediaType):
        classify_upload(filename, BytesIO(body))


def test_truncated_ooxml_header_is_not_accepted_as_a_presentation() -> None:
    with pytest.raises(UnsupportedMediaType):
        classify_upload("slide.pptx", BytesIO(b"PK\x03\x04synthetic"))


def test_valid_presentation_container_remains_supported() -> None:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    stream.seek(0)

    result = classify_upload("slide.pptx", stream)

    assert result.mime == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert result.preview is PreviewKind.DOWNLOAD


def test_text_validation_reads_past_the_initial_sniff_window() -> None:
    with pytest.raises(UnsupportedMediaType):
        classify_upload("late-binary.txt", BytesIO((b"a" * 9000) + b"\x00binary"))


def test_text_validation_accepts_utf8_codepoint_split_across_read_boundary() -> None:
    body = (b"a" * ((1024 * 1024) - 1)) + "€".encode() + b"\n"

    result = classify_upload("boundary.txt", BytesIO(body))

    assert result.mime == "text/plain"
    assert result.preview is PreviewKind.TEXT


def test_legacy_active_or_spoofed_content_is_forced_to_download() -> None:
    active = classify_stored_file(
        "legacy.html",
        BytesIO(b"<!doctype html><script>parent.pwned=1</script>"),
    )
    spoofed = classify_stored_file(
        "legacy.pdf",
        BytesIO(b"<!doctype html><script>parent.pwned=1</script>"),
    )

    assert active.mime == "application/octet-stream"
    assert active.preview is PreviewKind.DOWNLOAD
    assert spoofed.mime == "application/octet-stream"
    assert spoofed.preview is PreviewKind.DOWNLOAD
