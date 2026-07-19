"""Real EasyOCR pass over a generated image-only PDF.

Gated behind RAGHUB_TEST_OCR=1: the first run downloads EasyOCR's detection +
recognition models (~90 MB) to ~/.EasyOCR — too heavy for the default suite.
CI runs it in the nightly job; the live smoke (Task 15) covers it end-to-end.
"""

import io
import os

import pytest

from raghub.modules.documents.pipeline import parse_bytes

pytestmark = pytest.mark.skipif(
    not os.environ.get("RAGHUB_TEST_OCR"), reason="set RAGHUB_TEST_OCR=1 to run OCR e2e"
)


def _scanned_pdf_bytes() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 120), "EMERGENCY ASSEMBLY POINT", fill="black", font_size=64)
    draw.text((80, 260), "NORTH GATE CARPARK B", fill="black", font_size=64)
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def test_scanned_pdf_ocr_extracts_text() -> None:
    blocks = parse_bytes(_scanned_pdf_bytes(), "scan.pdf", ocr_enabled=True)
    joined = " ".join(b.text for b in blocks).upper()
    assert "ASSEMBLY" in joined
    assert "NORTH GATE" in joined
