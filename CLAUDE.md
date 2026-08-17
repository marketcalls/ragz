# Ragz — AI Session Rules

Ragz is a self-hosted, multi-tenant Agentic RAG platform (FastAPI + React + Qdrant + LiteLLM + Postgres). AGPL-3.0, fully open source.

**Source of truth:** this repository — the tests, the CI gates, and `docs/adr/`. See
[ADR-0005](docs/adr/ADR-0005-architecture-source-of-truth.md). (Earlier revisions
pointed at `docs/superpowers/specs/…`; those files were never tracked here, so every
reference to them resolved to nothing.) Product requirements: `docs/prd.md`. Current
remediation plan: `docs/audit/2026-08-17-architecture-review.md`.

A rule in this file must be executable — a test, or a CI gate — or explicitly marked
**Not implemented**. Never state an unbuilt control in the present tense: it reads as
evidence the control exists, and reviewers stop looking.

## The Five Iron Security Rules

1. **Tenant isolation has one code path per store.** Postgres queries on org-owned tables go through the `TenantContext` dependency (`modules/tenancy/`). Qdrant searches go through the single filter-building function in `modules/retrieval/` (tenant_id + workspace membership + ACL-group intersection). Nothing else constructs Qdrant filters.
2. **Document ACLs are enforced inside the vector query** — never post-filtered in Python. An answer must never cite a document the asking user cannot open. Adversarial leak tests live in `backend/tests/isolation/` and run on every PR. Restricted documents still appear in workspace document listings for plain members (existence is visible, Drive-style); only contents/citations/chunks are ACL-enforced in the vector query, and the `acl_group_ids` field itself is admin/superadmin-only metadata, blanked to `null` for plain users.
3. **Secrets live encrypted in Postgres** (envelope AES-256-GCM); the KEK is the only out-of-DB secret. Decryption happens in exactly one function in `modules/secrets/`. Secret fields are write-only in schemas. Secrets never appear in `.env` (beyond DB conn + KEK source), logs, traces, or API responses.
4. **AuthN/AuthZ are declarative at the route boundary**: Argon2id, 15-min JWT access + rotating refresh, permission checks as FastAPI dependencies per route — no inline role checks in handlers. Rate limiting on auth/chat endpoints.
5. **The LLM boundary treats documents as data and output as untrusted**: retrieved chunks wrapped in delimited data blocks; model output rendered as sanitized markdown only; agent tools read-only in v1.

Review bar: OWASP ASVS L2 + OWASP LLM Top 10.

## Module Map

| Module (`backend/src/ragz/modules/`) | Owns |
|---|---|
| `auth` | identity, sessions, API keys, (later) SSO |
| `tenancy` | orgs, workspaces, groups, membership, `TenantContext` |
| `documents` | upload, ingestion jobs, metadata, deletion propagation |
| `retrieval` | vector store client, hybrid search, rerank, ACL filter (ONE code path) |
| `chat` | conversations, streaming, citations, agent loop (Phase 3) |
| `models` | model registry, LiteLLM sync, capability probes |
| `quotas` | allocations, usage ledger, enforcement |
| `secrets` | envelope encryption, KEK handling |
| `audit` | append-only event log |
| `outbox` | durable intent to run background work; committed with the domain change that justifies it |

Boundaries: `api/` and `worker/` are thin entrypoints that call module `service.py` only. Modules import `core/` and other modules' public services only — never internals or ORM models. Direction: `api`/`worker` → `modules` → `core`. Enforced by import-linter in CI.

## Stack & Tooling

- **Backend:** Python 3.12, uv, FastAPI (fully async), SQLAlchemy 2.0 async, Alembic (forward-only), Pydantic v2, Celery + Redis (priority queues), structlog.
- **Frontend:** React + Vite, TS `strict`, pnpm, TanStack Query, shadcn/ui + Tailwind. API client generated from OpenAPI.
- **Commands:** `uv run pytest` · `uv run ruff check --fix` · `uv run mypy` · `pnpm test` · `pnpm lint` · `docker compose up` (canonical dev env).
- Conventional Commits. Architecture-changing decisions get an ADR in `docs/adr/`.
- CI gates (`.github/workflows/ci.yml`, per PR): ruff, mypy, import-linter, unit +
  integration (testcontainers — real Postgres/Qdrant/Redis, no mocked stores), the
  isolation suite as its own required job, Alembic single-head + chain apply, OpenAPI
  client drift, ESLint, tsc, Vitest, production build, Playwright compile, gitleaks.
  Dependency audit runs in `audit.yml`. **Not implemented:** SBOM and image scanning —
  blocked on there being no Dockerfiles yet (review item 7).

## Never Do

- No Qdrant filter construction outside `modules/retrieval/`.
- No raw queries on org-owned tables outside `TenantContext`.
- No ORM objects across module boundaries or in API responses — Pydantic schemas at every boundary.
- No secrets in `.env`, logs, or serialized responses.
- No blocking I/O in request handlers — CPU-heavy work goes to Celery.
- No bare `except:`; typed module exceptions → global RFC 9457 handler.
- No fetch-in-`useEffect` — server state goes through TanStack Query.
- No post-filtering of ACLs in Python.

## Error Handling & Observability

Typed exceptions per module → one global `application/problem+json` handler; no internal details in responses. `request_id`/`org_id`/`user_id` bound in structlog and propagated to workers. `/healthz` + `/readyz` on the API. Degradation contract: reranker down → fusion order; LLM error → fallback chain; Redis down → quotas fail closed, caches fail open.

**Not implemented** (review item 8, scored 3/15 — earlier revisions of this file
asserted all of it as if built): Prometheus metrics `ragz_<module>_<metric>`, per-stage
RAG latency histograms, OpenTelemetry tracing, trace propagation into Celery, worker
health endpoints, and alerting/SLOs. Neither `prometheus_client` nor `opentelemetry` is
a dependency today.

## Depth Pointers

Architecture & layout: the Module Map above, enforced by import-linter. Security model:
the Five Iron Rules above, enforced by `backend/tests/isolation/`. Coding standards:
ruff + mypy config in `backend/pyproject.toml`, ESLint + `tsconfig.json` in `frontend/`.
Testing strategy: `.github/workflows/ci.yml`. Decisions: `docs/adr/`. Known gaps and
sequencing: `docs/audit/2026-08-17-architecture-review.md`.

## Product Requirements Pointers (owner addendum 2026-07-19)

The addendum file itself was never tracked here (see ADR-0005); these non-negotiables
are transcribed so they survive it:
formats incl. PPTX + OCR for scanned PDFs; version-aware retrieval (latest approved
wins, superseded ignored); citations carry document name, version, section, page;
never answer without sufficient indexed grounding (no-answer mode stays on);
superadmin-composable custom roles; per-workspace metadata schema (name, version,
revision date, department, doc type). Implementation home: Plan H (after Plan G).

**Parser ↔ page citations (2026-08-15):** the `page` in a citation is only as good
as the parser. `anydoc` (pure-Rust → flat Markdown) has **no page boundaries**, so it
stamps `page=1` on every chunk — citations then always say page 1. `document_parser`
app-setting options: **`liteparse`** (default — run-llama, PDFium, self-hosted/offline,
per-page `page_num`), `docling` (self-hosted, page-accurate, slow), `llamaparse`
(cloud, needs a key), `anydoc` (fastest, but no page numbers). **Benchmark** (36 MB /
168-page PDF, same machine): **liteparse 2.6 s (page-accurate)** · anydoc 0.92 s (NO
pages) · docling 103 s (page-accurate). liteparse = ~40× faster than docling at equal
page accuracy, only ~3× slower than page-blind anydoc → the default. Page is baked into chunks at PARSE time, so
switching parsers requires a **re-parse** of existing docs (`delete_document_points` →
`build_ingest_chain`), not just a reembed. Current default: `liteparse`.
