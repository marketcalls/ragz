# Backend image: serves the API, the Celery worker, the beat scheduler, and the
# one-shot migration job. One image, four commands -- they share a codebase and
# a dependency set, so separate images would only guarantee they could drift.
#
# Review item 7: until this existed there was no reproducible deployment at all;
# deploy/compose.yaml ran the backing stores and nothing else, so "the reference
# single-node deployment" never actually ran the application.

# --- builder -----------------------------------------------------------------
# uv resolves into a venv here so the runtime stage carries no build toolchain,
# no uv, and no lockfile -- less to patch, and nothing to compile at deploy time.
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, as their own layer: application edits are frequent and
# dependency changes are not, so a code change must not re-resolve the world.
# --locked fails loudly rather than silently resolving something new.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# libmagic: content-type sniffing in the ingest pipeline. Nothing else is added;
# every extra package is attack surface that has to be patched forever.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root, and it owns nothing it does not need to write. The KEK and any
# secrets arrive as mounts, never baked into a layer (iron rule 3).
RUN groupadd --system --gid 1001 ragz \
    && useradd --system --uid 1001 --gid ragz --create-home ragz

WORKDIR /app
COPY --from=builder --chown=ragz:ragz /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER ragz
EXPOSE 8000

# Overridden per role in compose:
#   worker:    celery -A ragz.worker.celery_app:celery_app worker -Q interactive,default,maintenance
#   beat:      celery -A ragz.worker.celery_app:celery_app beat
#   migrate:   alembic upgrade head
CMD ["uvicorn", "--factory", "ragz.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
