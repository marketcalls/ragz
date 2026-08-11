"""anydoc dependency smoke test: the wheel installs and converts bytes to
Markdown. Gates the whole anydoc feature (no wheel -> no feature)."""

import anydoc


def test_anydoc_converts_csv_bytes_to_markdown() -> None:
    md = anydoc.to_markdown_bytes(b"name,age\nAlice,30\nBob,25\n", "csv")
    assert isinstance(md, str)
    assert "Alice" in md and "Bob" in md


def test_anydoc_module_exposes_convert_error() -> None:
    # Task 3 maps this base class onto IngestFailure; assert it exists so a
    # future anydoc API change that renames it fails loudly here.
    assert hasattr(anydoc, "ConvertError")
