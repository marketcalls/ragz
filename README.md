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
RAGHUB_BOOTSTRAP_EMAIL=root@raghub.internal RAGHUB_BOOTSTRAP_PASSWORD=changeme123 \
  uv run python -m raghub.bootstrap
uv run uvicorn --factory raghub.api.app:create_app --port 8000
# worker (second terminal, from backend/): see Plan B section of the worker README

# 3. Frontend (from frontend/)
cd frontend && pnpm install
pnpm generate:api   # regenerates src/api/schema.d.ts from the running backend
pnpm dev            # http://localhost:5173 (proxies /api → :8000)
```

Sign in with the bootstrap superadmin, add a model under Superadmin › Models, create a
workspace, upload documents, chat.
