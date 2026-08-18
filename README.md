# Ragz

**Self-hosted, multi-tenant Agentic RAG platform.** Upload your documents, ask
questions, get cited answers — with rich generative-UI responses (cards, charts,
tables, source cards), optional web search, and enterprise access control.
FastAPI + React + Qdrant + LiteLLM + Postgres. AGPL-3.0.

📖 **Documentation: [ragz.net](https://ragz.net)** — installation guides
(Linux / macOS / Windows / Docker), configuration, models & parsers, RBAC, the
API reference, and production deployment.

---

## What you get

- **Grounded chat.** Streaming answers with citations (document, version, page,
  section). Says "I don't have enough to answer" instead of hallucinating.
- **Rich generative UI.** Answers can render as visual cards, charts, tables,
  clickable source cards, follow-up chips, and accordions — not just plain text.
- **Web search.** Toggle per turn; answers can cite public web pages (DuckDuckGo
  by default, Tavily optional).
- **Multi-tenant + RBAC.** Orgs, workspaces, groups; `superadmin/admin/user`
  tiers plus custom role templates. Document ACLs enforced *inside* the vector
  query — an answer can never cite a doc you can't open. Append-only audit log.
- **Any model.** Point at OpenAI / Anthropic / Gemini / any LiteLLM provider, or
  run fully local (Ollama / vLLM + local embeddings/rerank) for air-gapped use.
- **Documents.** PPTX/DOCX/PDF and more; OCR for scanned PDFs; version-aware
  retrieval (latest approved wins).
- **Integrations.** OpenAI-compatible API endpoint (per-user/workspace keys) and
  inbound bots for Telegram, Discord, Slack.
- **Secrets encrypted.** All provider keys are AES-256-GCM envelope-encrypted in
  Postgres under a single out-of-DB key (the KEK); passwords are Argon2id-hashed.
  Keys are write-only in the API and never logged.

---

## Quick start (local, ~10 minutes)

**Prerequisites:** Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/),
Node 20+ + pnpm.

```bash
# 1. Start infrastructure (Postgres, Qdrant, Redis, MinIO, LiteLLM, embeddings)
docker compose -f deploy/compose.yaml up -d

# 2. Backend — from backend/
cd backend
uv sync
uv run alembic upgrade head
RAGZ_BOOTSTRAP_EMAIL=admin@example.com RAGZ_BOOTSTRAP_PASSWORD=changeme12345 \
  uv run python -m ragz.bootstrap                       # creates the first superadmin
uv run uvicorn --factory ragz.api.app:create_app --port 8000

# 3. Worker — second terminal, from backend/  (add --pool=solo on macOS)
uv run celery -A ragz.worker.celery_app:celery_app worker -Q interactive,default,maintenance -l info

# 4. Scheduler — third terminal, from backend/  (syncs the model catalog)
uv run celery -A ragz.worker.celery_app:celery_app beat -l info

# 5. Frontend — from frontend/
cd ../frontend
pnpm install
pnpm generate:api      # generate the typed API client from the running backend
pnpm dev               # http://localhost:5173
```

Then open **http://localhost:5173**, sign in with the bootstrap superadmin, and:
**Admin › Models** → add a model (e.g. an OpenAI key) → create a workspace →
upload documents → chat.

---

## Configuration you'll actually touch

**Model placement drives your RAM.** Everything except the model servers idles at
~1–1.5 GB. You choose embeddings/rerank placement per install:

| Setup | Local model servers | Host RAM |
|---|---|---|
| **Cloud** — API embeddings (OpenAI) + API rerank (Cohere) | none | **4–5 GB** |
| **Hybrid** — one local (embeddings *or* rerank) | one | **8–10 GB** |
| **Fully local / air-gapped** — local `bge-m3` + local rerank | two | **12–16 GB** |

The generation LLM lives outside this table: use a cloud API (recommended — no
GPU) or a local LLM (Ollama/vLLM, needs a 16 GB+ GPU). Disk: budget **80–100 GB**
SSD (≈25–30 GB fixed for images + model weights, the rest grows with your corpus).

**Document parsing** (superadmin-selectable in *Settings*):
- **liteparse** — recommended; local, page-accurate citations, ~2–3 s for a
  168-page PDF.
- **anydoc** — fastest, but no page numbers (every citation shows page 1).
- **Docling** — local, page-accurate, OCRs scanned PDFs (EasyOCR), slower.
- **LlamaParse** — cloud (needs an API key).

Scanned/image-only PDFs auto-fall back to Docling OCR. First OCR use downloads
~90 MB of models to `~/.EasyOCR` on the worker host; disable with
`RAGZ_OCR_ENABLED=false`.

---

## Going to production

The compose file in `deploy/` is the reference single-node deployment. Before
exposing it beyond localhost:

1. **Change every dev credential** in `deploy/compose.yaml`: `LITELLM_MASTER_KEY`,
   `LITELLM_SALT_KEY`, the Postgres and MinIO passwords, and your
   `RAGZ_BOOTSTRAP_PASSWORD` (12+ chars).
2. **Back up two things**: the Postgres volume (`pgdata`) and the **KEK file**
   (`backend/data/ragz_kek`, or `RAGZ_KEK_FILE`). Losing the KEK makes every
   stored provider key unrecoverable.
3. **Put it behind a reverse proxy with TLS.** All compose services bind to
   `127.0.0.1` by design — only your proxy should be reachable. Pass the real
   client IP (`X-Forwarded-For`) so rate limiting works.
4. **Scale** by adding Celery workers (ingestion) and uvicorn processes (chat).
   Move Postgres to a managed instance with its own backups for real traffic.

Full sizing, air-gapped, and hardening notes:
**[ragz.net/docs/deploy/production](https://ragz.net/docs/deploy/production)**.

---

## Development

- **Backend:** `uv run pytest` · `uv run ruff check` · `uv run mypy` (from `backend/`)
- **Frontend:** `pnpm test` · `pnpm lint` · `pnpm build` (from `frontend/`)
- Postgres/Qdrant/Redis run as real services in tests (testcontainers) — no mocks.

## License

AGPL-3.0. See [LICENSE](LICENSE).
