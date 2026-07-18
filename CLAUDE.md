# RagHub — AI Session Rules

RagHub is a self-hosted, multi-tenant Agentic RAG platform (FastAPI + React + Qdrant + LiteLLM + Postgres). AGPL-3.0, fully open source.

**Source of truth:** `docs/superpowers/specs/2026-07-18-raghub-engineering-foundation-design.md` (the Foundation). Product requirements: `docs/prd.md`. This file is distilled from the Foundation — if they disagree, the Foundation wins; fix this file in the same commit.

## The Five Iron Security Rules

1. **Tenant isolation has one code path per store.** Postgres queries on org-owned tables go through the `TenantContext` dependency (`modules/tenancy/`). Qdrant searches go through the single filter-building function in `modules/retrieval/` (tenant_id + workspace membership + ACL-group intersection). Nothing else constructs Qdrant filters.
2. **Document ACLs are enforced inside the vector query** — never post-filtered in Python. An answer must never cite a document the asking user cannot open. Adversarial leak tests live in `backend/tests/isolation/` and run on every PR.
3. **Secrets live encrypted in Postgres** (envelope AES-256-GCM); the KEK is the only out-of-DB secret. Decryption happens in exactly one function in `modules/secrets/`. Secret fields are write-only in schemas. Secrets never appear in `.env` (beyond DB conn + KEK source), logs, traces, or API responses.
4. **AuthN/AuthZ are declarative at the route boundary**: Argon2id, 15-min JWT access + rotating refresh, permission checks as FastAPI dependencies per route — no inline role checks in handlers. Rate limiting on auth/chat endpoints.
5. **The LLM boundary treats documents as data and output as untrusted**: retrieved chunks wrapped in delimited data blocks; model output rendered as sanitized markdown only; agent tools read-only in v1.

Review bar: OWASP ASVS L2 + OWASP LLM Top 10.

## Module Map

| Module (`backend/src/raghub/modules/`) | Owns |
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

Boundaries: `api/` and `worker/` are thin entrypoints that call module `service.py` only. Modules import `core/` and other modules' public services only — never internals or ORM models. Direction: `api`/`worker` → `modules` → `core`. Enforced by import-linter in CI.

## Stack & Tooling

- **Backend:** Python 3.12, uv, FastAPI (fully async), SQLAlchemy 2.0 async, Alembic (forward-only), Pydantic v2, Celery + Redis (priority queues), structlog.
- **Frontend:** React + Vite, TS `strict`, pnpm, TanStack Query, shadcn/ui + Tailwind. API client generated from OpenAPI.
- **Commands:** `uv run pytest` · `uv run ruff check --fix` · `uv run mypy` · `pnpm test` · `pnpm lint` · `docker compose up` (canonical dev env).
- Conventional Commits. Architecture-changing decisions get an ADR in `docs/adr/`.
- CI gates (all required): ruff, mypy, ESLint, tsc, unit + integration (testcontainers — real Postgres/Qdrant/Redis, no mocked stores), isolation suite, import-linter, dependency audit, image scan.

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

Typed exceptions per module → one global `application/problem+json` handler; no internal details in responses. `request_id`/`org_id`/`user_id` bound in structlog and propagated to workers. Prometheus metrics `raghub_<module>_<metric>`; per-stage RAG latency histograms; OpenTelemetry tracing; `/healthz` + `/readyz` on both processes. Degradation contract: reranker down → fusion order; LLM error → fallback chain; Redis down → quotas fail closed, caches fail open.

## Depth Pointers (Foundation sections)

Architecture & layout §2 · Security model §3 · Coding standards §4 · Testing strategy §5 · Observability/ops §6 · Phase scoping §8.

## Product Requirements Pointers (owner addendum 2026-07-19)

Binding product requirements beyond the PRD live in
`docs/superpowers/specs/2026-07-19-customer-requirements-addendum.md`. Non-negotiables:
formats incl. PPTX + OCR for scanned PDFs; version-aware retrieval (latest approved
wins, superseded ignored); citations carry document name, version, section, page;
never answer without sufficient indexed grounding (no-answer mode stays on);
superadmin-composable custom roles; per-workspace metadata schema (name, version,
revision date, department, doc type). Implementation home: Plan H (after Plan G).
