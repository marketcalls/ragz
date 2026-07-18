# RagHub

Self-hosted, multi-tenant RAG platform. Specs live in `docs/superpowers/specs/`.

## Dev Setup

Prereqs: Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+ + pnpm.

```bash
# 1. Infrastructure
docker compose -f deploy/compose.yaml up -d

# 2. Backend (from backend/)
cd backend && uv sync
uv run alembic upgrade head
RAGHUB_BOOTSTRAP_EMAIL=root@raghub.internal RAGHUB_BOOTSTRAP_PASSWORD=changeme12345 \
  uv run python -m raghub.bootstrap
uv run uvicorn --factory raghub.api.app:create_app --port 8000
# worker (second terminal, from backend/):
uv run celery -A raghub.worker.celery_app:celery_app worker -Q interactive,default -l info
# macOS note: add --pool=solo — Docling's native libraries crash under the default
# prefork pool (fork-safety); Linux deployments can keep prefork.

# 3. Frontend (from frontend/)
cd frontend && pnpm install
pnpm generate:api   # regenerates src/api/schema.d.ts from the running backend
pnpm dev            # http://localhost:5173 (proxies /api → :8000)
```

Sign in with the bootstrap superadmin, add a model under Superadmin › Models, create a
workspace, upload documents, chat.

## Deployment notes (single-node)

The compose file in `deploy/` is the reference single-node deployment. Before exposing
an instance beyond localhost:

1. **Change every dev credential**: `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`
   (encrypts provider keys at rest in LiteLLM's own DB — changing it later requires
   wiping the `litellm` database, which is safe: RagHub replays the full model config),
   the Postgres and MinIO passwords in `deploy/compose.yaml`, and your
   `RAGHUB_BOOTSTRAP_PASSWORD` (12+ chars).
2. **Back up two things**: the Postgres volume (`pgdata`) and the KEK file
   (`backend/data/raghub_kek` by default, configurable via `RAGHUB_KEK_FILE`).
   Losing the KEK makes every stored provider key unrecoverable; re-enter keys via
   Superadmin › Models after generating a new one.
3. **Embeddings**: TEI serves `BAAI/bge-m3` (1024-dim) by default and needs ~4 GB+
   RAM; on memory-constrained or ARM-emulated hosts, point TEI at a smaller model
   (e.g. `BAAI/bge-small-en-v1.5`) and set `RAGHUB_EMBEDDING_DIM` to match.
   The embedding model is locked per workspace after first indexing.
4. **Workers**: run one or more `celery ... worker` processes; the default prefork
   pool is correct on Linux (`--pool=solo` is only a macOS dev workaround). Scale
   ingestion by adding workers.
5. **TLS / fronting**: put the API and frontend behind a reverse proxy with TLS.
   Rate limiting keys on client IP — configure your proxy to pass the real address
   (X-Forwarded-For handling lands with the deployment hardening pass).
6. **Ports**: all compose services bind 127.0.0.1 by design; only your reverse
   proxy should be reachable from outside.
