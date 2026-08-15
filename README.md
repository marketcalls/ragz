# Ragz

Self-hosted, multi-tenant Agentic RAG platform (FastAPI + React + Qdrant + LiteLLM +
Postgres), AGPL-3.0. Specs live in `docs/superpowers/specs/`.

## Features

- **Multi-tenant isolation.** Orgs, workspaces, and groups. Document ACLs are enforced
  *inside* the vector query (never post-filtered), so an answer can never cite a document
  the asking user can't open. Restricted docs still appear in listings (Drive-style
  existence), only their contents/citations are gated.
- **Role-based access control.** `superadmin` / `admin` / `user` tiers plus composable
  custom **role templates** (draft → active lifecycle, monotonic versioning, rollback with
  immutable history). Deny-by-default permissions; a central `require_action` policy with a
  CI gate that fails on any unclassified route; append-only audit log (DB-trigger enforced)
  that records **denials** as well as successes, with an org-scoped, `audit.read`-gated
  export. Admins need an explicit content-relationship to read restricted documents
  (no automatic ACL bypass).
- **Pluggable document parsing** (superadmin-selectable in *Settings*):
  - **anydoc** *(default)* — Firecrawl's pure-Rust converter; office formats + text PDFs to
    clean Markdown in single-digit ms.
  - **Docling** — local; **OCRs scanned/image-only PDFs** (EasyOCR). anydoc auto-falls back
    to Docling-OCR for scans, and `.txt` routes to Docling, so those keep working under the
    anydoc default.
  - **LlamaParse** — cloud (API key).
  - Version-aware retrieval (latest-approved wins); citations carry document name, version,
    section, and page.
- **Hybrid retrieval + rerank.** Dense (`bge-m3` via TEI) + sparse, with an optional
  reranker — local TEI or **Cohere Rerank v4** (fast/pro) — and a lexical fallback.
- **Chat.** Streaming answers with citations, no-answer mode when grounding is insufficient,
  and an agent loop.
- **Integrations.** A secure external API (superadmin-managed, per-user/workspace API keys;
  OpenAI-compatible chat endpoint) and inbound **bots** for Telegram, Discord, and Slack.
- **Secrets.** Provider keys stored envelope-encrypted (AES-256-GCM) in Postgres; the only
  out-of-DB secret is the KEK. Keys are write-only in the API and never logged or returned.

## Dev Setup

Prereqs: Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+ + pnpm.

```bash
# 1. Infrastructure
docker compose -f deploy/compose.yaml up -d

# 2. Backend (from backend/)
cd backend && uv sync
uv run alembic upgrade head
RAGZ_BOOTSTRAP_EMAIL=root@ragz.internal RAGZ_BOOTSTRAP_PASSWORD=changeme12345 \
  uv run python -m ragz.bootstrap
uv run uvicorn --factory ragz.api.app:create_app --port 8000
# worker (second terminal, from backend/):
uv run celery -A ragz.worker.celery_app:celery_app worker -Q interactive,default -l info
# macOS note: add --pool=solo — Docling's native libraries crash under the default
# prefork pool (fork-safety); Linux deployments can keep prefork.
# beat (third terminal, from backend/) — scheduled jobs (model catalog sync):
uv run celery -A ragz.worker.celery_app:celery_app beat -l info
```

**Parsing & OCR.** anydoc is the default parser (fast, no models); scanned/image-only PDFs
auto-fall back to Docling, whose ingestion worker OCRs low-text PDFs automatically via EasyOCR.
First use downloads ~90 MB of models to `~/.EasyOCR` **on the machine running the worker** —
pre-fetch with `uv run python -c "import easyocr; easyocr.Reader(['en'])"`. Air-gapped
installs: copy a populated `~/.EasyOCR` directory into the worker's home. arm64 (Apple
Silicon / Graviton): EasyOCR runs on standard PyTorch CPU wheels — no extra system packages
(this is why EasyOCR over Tesseract). Disable globally with `RAGZ_OCR_ENABLED=false`.

```bash
# 3. Frontend (from frontend/)
cd frontend && pnpm install
pnpm generate:api   # regenerates src/api/schema.d.ts from the running backend
pnpm dev            # http://localhost:5173 (proxies /api → :8000)
```

Sign in with the bootstrap superadmin, add a model under Superadmin › Models, create a
workspace, upload documents, chat.

## Deployment notes (single-node)

### Hardware sizing

The dominant cost driver is the **generation LLM**, so sizing forks on whether you run it
locally or via a cloud API. Everything else (Postgres, Qdrant, Redis, MinIO, LiteLLM,
Dex, the two TEI model servers) is modest.

| | **Recommended (cloud LLM)** | **Fully self-hosted / air-gapped** |
|---|---|---|
| Generation model | OpenAI / Anthropic / Gemini via LiteLLM — no local weights | Local LLM (Ollama / vLLM) — **GPU required** |
| RAM | **4–16 GB** (see below — driven by embedding/rerank placement) | 32–64 GB |
| Disk (SSD) | **80–100 GB** | 150–250 GB |
| vCPU | 4 min · 8 recommended | 8+ min |
| GPU | not required | required (16 GB+ VRAM; CPU inference is impractically slow) |

For a startup, run the whole stack on **one 4–8 vCPU / 100 GB Linux VM** and pay per-token
for generation. How much RAM depends entirely on **which model services run locally** vs.
via an API — the database side (Postgres, Qdrant, Redis, MinIO, LiteLLM) idles at only
~1–1.5 GB.

**RAM is a function of model placement.** The two CPU-bound TEI model servers are the whole
story — each is optional and runs only if you choose the local backend:

| Deployment | Local model servers | RAM (host) |
|---|---|---|
| **Fully hosted** — API embeddings (e.g. OpenAI) + API rerank (e.g. Cohere) | none | **4–5 GB** (measured ~1.7 GB in use) |
| **Hybrid** — one local (local embeddings *or* local reranker) | one TEI server (~4–6 GB) | **8–10 GB** |
| **Fully local / air-gapped** — local `bge-m3` embeddings **and** local `tei-rerank` | two TEI servers | **12–16 GB** |

Embeddings are locked per-workspace and reranking is a per-workspace/global toggle with a
Cohere-API or lexical fallback — so you pick a point on this table per install. **Document
parsing is no longer a RAM driver**: the default parser is **liteparse** (PDFium, runs in
the Celery worker, ~2–3 s for a 168-page PDF); Docling (heavier, layout-model OCR) stays
available as an option but isn't the default. On Apple-Silicon dev the two TEI servers run
under amd64 emulation and roughly *double* in RAM — budget the top of each range.

**Why ~80–100 GB disk.** Roughly 25–30 GB is fixed overhead before any user data — Docker
images (~4 GB; LiteLLM is the largest), embedding + rerank model weights (~4–5 GB; the
`bge-m3` volume alone is ~2.4 GB), plus OS and app. Corpus then grows disk linearly: MinIO
stores each document *plus* its extracted `blocks.json` + `chunks.json` (~2–2.5× the raw
file size), and Qdrant stores dense + sparse + up to 3× hypothetical-question vectors per
chunk (budget ~2–3 GB per ~100 K chunks).

**What scales it up:** corpus size → disk; concurrent users → RAM/vCPU for uvicorn +
Celery; heavy OCR volume → a dedicated ingestion worker; local LLM / air-gapped → the GPU
tier above. For a paying production customer, move Postgres to a managed instance with its
own backups (see below).

### Going live

The compose file in `deploy/` is the reference single-node deployment. Before exposing
an instance beyond localhost:

1. **Change every dev credential**: `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`
   (encrypts provider keys at rest in LiteLLM's own DB — changing it later requires
   wiping the `litellm` database, which is safe: Ragz replays the full model config),
   the Postgres and MinIO passwords in `deploy/compose.yaml`, and your
   `RAGZ_BOOTSTRAP_PASSWORD` (12+ chars).
2. **Back up two things**: the Postgres volume (`pgdata`) and the KEK file
   (`backend/data/ragz_kek` by default, configurable via `RAGZ_KEK_FILE`).
   Losing the KEK makes every stored provider key unrecoverable; re-enter keys via
   Superadmin › Models after generating a new one.
3. **Embeddings**: TEI serves `BAAI/bge-m3` (1024-dim) by default and needs ~4 GB+
   RAM; on memory-constrained or ARM-emulated hosts, point TEI at a smaller model
   (e.g. `BAAI/bge-small-en-v1.5`) and set `RAGZ_EMBEDDING_DIM` to match.
   The embedding model is locked per workspace after first indexing.
4. **Workers**: run one or more `celery ... worker` processes; the default prefork
   pool is correct on Linux (`--pool=solo` is only a macOS dev workaround). Scale
   ingestion by adding workers.
5. **TLS / fronting**: put the API and frontend behind a reverse proxy with TLS.
   Rate limiting keys on client IP — configure your proxy to pass the real address
   (X-Forwarded-For handling lands with the deployment hardening pass).
6. **Ports**: all compose services bind 127.0.0.1 by design; only your reverse
   proxy should be reachable from outside.
7. **Air-gapped installs**: chat token counting downloads a tiktoken encoding on
   first use; bake the tiktoken cache (set `TIKTOKEN_CACHE_DIR` and pre-populate it
   in the image) for exact counts offline, or accept the automatic character-based
   estimate fallback if the download can't happen.
