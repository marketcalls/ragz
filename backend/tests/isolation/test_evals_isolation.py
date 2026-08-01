"""Iron rule 1 pin for the eval runner (§6): no Qdrant filter construction
outside modules/retrieval/ — the runner calls retrieve() ONLY."""

from pathlib import Path

import ragz


def test_evals_module_constructs_no_qdrant_filters() -> None:
    evals_dir = Path(ragz.__file__).parent / "modules" / "evals"
    for path in evals_dir.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "qdrant_client" not in src, f"{path.name} must not import qdrant"
        assert "_tenant_filter" not in src, f"{path.name} must not reach the filter builder"
