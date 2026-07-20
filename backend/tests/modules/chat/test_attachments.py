import os

import pytest


def test_extract_text_from_plain_document(monkeypatch) -> None:
    from raghub.modules.chat.attachments import extract_text
    text = extract_text(b"Hello attachment world", "notes.txt")
    assert "Hello attachment world" in text


@pytest.mark.skipif(
    not os.environ.get("RAGHUB_TEST_OCR"), reason="set RAGHUB_TEST_OCR=1 to run OCR e2e"
)
def test_extract_text_from_image_runs_ocr_by_default(tmp_path) -> None:
    # Uses a real tiny PNG with rendered text — generate with PIL in the test
    # fixture setup, or use a checked-in tiny fixture PNG under tests/fixtures/
    # if one already exists in this repo (check first). Docling's default
    # image pipeline has do_ocr=True (confirmed at plan-writing time), so this
    # is a real OCR run, not a mock.
    import io

    from PIL import Image, ImageDraw

    from raghub.modules.chat.attachments import extract_text
    img = Image.new("RGB", (300, 80), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "ATTACHMENT TEST", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    text = extract_text(buf.getvalue(), "photo.png")
    assert "ATTACHMENT" in text.upper()
