"""Dump the FastAPI OpenAPI document to a file without starting a server.

CI compares `openapi-typescript` output from this against the committed
`frontend/src/api/schema.d.ts`, so a route or schema change that never had the
client regenerated fails the build instead of surfacing as a runtime type lie.

Usage: uv run python scripts/export_openapi.py <output.json>
"""

from __future__ import annotations

import json
import pathlib
import sys

from ragz.api.app import create_app


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = pathlib.Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    # create_app() builds the router tree; .openapi() is pure derivation from
    # it -- no network, no database, no lifespan.
    # NOT sort_keys: the served document preserves route declaration order, and
    # openapi-typescript emits members in document order. Sorting here would
    # reorder the generated client and fail the drift diff on ordering alone.
    out.write_text(json.dumps(create_app().openapi(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
