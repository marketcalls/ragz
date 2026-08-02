# Ragz, Product Requirements Document
## Ragz: Self-Hosted Agentic RAG Platform for Business

**Version:** 1.0 draft
**Date:** July 2026
**Status:** Planning
**Stack:** FastAPI + React + ShadcnUI + Qdrant + LiteLLM + PostgreSQL

---

## 1. Product Overview

### 1.1 One-line summary
Ragz is a self-hostable, multi-tenant Agentic RAG platform that lets a business chat with its private documents (up to hundreds of GB) using any LLM provider or fully local models, with enterprise-grade admin controls, citations, and professional output rendering.

### 1.2 Problem statement
Businesses want AI over their internal knowledge but face four blockers: data cannot leave their infrastructure, no single LLM vendor can be a hard dependency, off-the-shelf tools (AnythingLLM, OpenWebUI) lack enterprise controls and break down at large corpus sizes, and building from scratch requires deep retrieval expertise. This product resolves all four: self-hosted, model-agnostic via LiteLLM, built for 100GB-500GB+ corpora with agentic retrieval, and shipped with the RBAC, audit, and compliance features businesses actually procure against.

### 1.3 Goals
- Answer questions over a 500GB corpus with sub-second retrieval and streaming answers with verifiable citations.
- Zero mandatory external dependency: every component (LLM, embeddings, reranker, vector DB, storage) can run on-premises.
- BYOK for any hosted provider, local models as first-class citizens.
- Admin experience good enough that a non-technical IT admin can run the platform.
- Output quality (tables, charts, citations, exports) that reads as a professional business tool, not a chatbot demo.

### 1.4 Non-goals (v1)
- Fine-tuning or training models.
- Real-time collaborative editing of documents.
- Mobile native apps (responsive web only in v1).
- Agent actions that write to external systems (v1 agents only read/retrieve).

---

## 2. Personas

| Persona | Description | Primary needs |
|---|---|---|
| **Superadmin** | Platform owner / IT lead who deploys and operates the instance | Provider keys, budgets, org management, audit, backups, upgrades |
| **Org Admin** | Department or company admin | User management, document library, model access, retrieval tuning, usage visibility |
| **Knowledge User** | Employee asking questions | Fast accurate answers, citations, attachments, history, exports |
| **Auditor / Compliance officer** | Reviews platform usage | Immutable audit logs, data retention proof, access reports |
| **Developer (API consumer)** | Integrates RAG into other internal tools | REST API, API keys, webhooks, embeddable widget |

---

## 3. Functional Requirements

### 3.1 Authentication and Identity

| ID | Requirement | Priority |
|---|---|---|
| AUTH-1 | Email + password login with JWT access/refresh tokens, argon2 hashing | P0 |
| AUTH-2 | SSO via OIDC (Google Workspace, Microsoft Entra ID, Okta, Keycloak) | P0 for enterprise sales, P1 for MVP |
| AUTH-3 | SAML 2.0 support | P2 |
| AUTH-4 | SCIM provisioning (auto user create/deactivate from IdP) | P2 |
| AUTH-5 | MFA (TOTP) | P1 |
| AUTH-6 | Invite-based onboarding with expiring links, domain allowlists per org | P0 |
| AUTH-7 | Session management: view/revoke active sessions, configurable session lifetime | P1 |
| AUTH-8 | API keys per org and per user with scopes and expiry | P1 |

### 3.2 RBAC and Multi-tenancy

| ID | Requirement | Priority |
|---|---|---|
| RBAC-1 | Three built-in roles: Superadmin, Admin (org-scoped), User | P0 |
| RBAC-2 | Custom roles with granular permissions (upload, delete, configure, view analytics) | P2 |
| RBAC-3 | Organizations as hard isolation boundary: Qdrant payload-based tenancy (tenant_id filter on every query), Postgres row-level org scoping enforced in a shared dependency | P0 |
| RBAC-4 | Workspaces within an org: a workspace groups documents + settings + members; users see only workspaces they belong to | P0 |
| RBAC-5 | **Document-level ACLs**: a document can be restricted to specific groups/users; retrieval must filter to documents the asking user can access. This is the most commonly missed enterprise requirement: an answer must never cite a document the user cannot open | P0 |
| RBAC-6 | Groups (map to IdP groups when SSO is used) for assigning workspace and document access | P1 |

### 3.3 Document Management and Ingestion

| ID | Requirement | Priority |
|---|---|---|
| DOC-1 | Upload: PDF, DOCX, XLSX, PPTX, CSV, TXT, MD, HTML, common image formats. Drag-drop, folder upload, bulk zip | P0 |
| DOC-2 | Async ingestion pipeline: parse (Docling) -> semantic chunk -> embed (TEI + bge-m3 default) -> upsert to Qdrant. Job status visible per document (queued / processing / indexed / failed with reason) | P0 |
| DOC-3 | OCR path for scanned PDFs and images inside documents (Tesseract or vision-model assisted), auto-detected on low-text pages | P1 |
| DOC-4 | Connectors: S3/MinIO bucket sync, SharePoint, Google Drive, Confluence, network share (SMB/NFS), website crawler. Scheduled re-sync with change detection (hash/etag) | P1 (S3 + Drive first) |
| DOC-5 | Document versioning: re-uploading replaces vectors atomically, previous version retained per retention policy | P1 |
| DOC-6 | Metadata schema per workspace (department, doc type, date, custom fields) used for retrieval filters and the agent's search_by_metadata tool | P0 |
| DOC-7 | Deduplication by content hash across a workspace | P1 |
| DOC-8 | Deletion propagates: Postgres record, object storage blob, and Qdrant vectors removed within minutes, with audit entry (GDPR/DPDP erasure support) | P0 |
| DOC-9 | Per-chat ephemeral attachments (docs + images) with inline-vs-retrieval routing and TTL cleanup (see documents_api.py design) | P0 |
| DOC-10 | Embedding model is locked per workspace after first index; changing it triggers an explicit, admin-confirmed full re-embed job with progress tracking | P0 |

### 3.4 Chat and Retrieval (Core Experience)

| ID | Requirement | Priority |
|---|---|---|
| CHAT-1 | Streaming answers via SSE, including agent activity events (tool calls, retrieved sources, rerank status) rendered as a live timeline | P0 |
| CHAT-2 | Hybrid retrieval: dense + sparse (bge-m3 dual output) with RRF fusion, metadata pre-filtering, cross-encoder reranking (bge-reranker-v2-m3) on top-50 | P0 |
| CHAT-3 | Agentic mode: hand-rolled tool loop (search, search_by_metadata, get_document, search_attachment), max 4 iterations, with a cheap router deciding agentic vs single-shot per query | P0 |
| CHAT-4 | Inline citations [n] resolving to a source panel: document name, page, chunk text, relevance score, deep link to view the document at that page. Attachment-origin citations visually distinct from library citations | P0 |
| CHAT-5 | In-app document viewer (PDF.js) with the cited chunk highlighted | P1 |
| CHAT-6 | Conversation history: persistent, searchable, org-retention-policy governed. Rename, pin, delete, share within org (link with permission check) | P0 |
| CHAT-7 | Follow-up awareness: conversation context carried across turns with a rolling token budget and summarization of old turns | P0 |
| CHAT-8 | Suggested follow-up questions after each answer (generated, toggleable per org) | P2 |
| CHAT-9 | "No answer found" honesty mode: if rerank scores fall below threshold, say so and show nearest sources rather than hallucinate; threshold tunable per workspace | P0 |
| CHAT-10 | Feedback: thumbs up/down + optional comment per answer, feeding the analytics and eval loop | P0 |
| CHAT-11 | Export answer: copy as markdown, download as PDF/DOCX (with citations appendix), copy tables as CSV | P1 |
| CHAT-12 | Multilingual: retrieval works cross-language (bge-m3 is multilingual); UI localization framework in place, English shipped first | P2 |
| CHAT-13 | Prompt templates / saved prompts per workspace ("Summarize this contract's obligations") surfaced as quick actions | P1 |

### 3.5 Model Management (BYOK + Local)

| ID | Requirement | Priority |
|---|---|---|
| MODEL-1 | LiteLLM Proxy as the single gateway. Superadmin registers providers (Anthropic, OpenAI, Azure OpenAI, Gemini, Bedrock, Groq, Mistral, DeepSeek) and local endpoints (Ollama, vLLM, LM Studio, any OpenAI-compatible URL) | P0 |
| MODEL-2 | Virtual keys per org issued by LiteLLM Proxy; raw provider keys never leave the proxy config; write-only key fields in UI (never redisplayed) | P0 |
| MODEL-3 | Model registry with capability flags: supports_vision, supports_tools, context_window, cost per 1M tokens, latency class. Agentic mode auto-disabled for models flagged unreliable at tool calling (plain RAG fallback) | P0 |
| MODEL-4 | Org admins choose which registered models their users may select; per-workspace default model | P0 |
| MODEL-5 | Budgets and rate limits per org and per user (LiteLLM native), with soft-limit warnings and hard-limit blocks surfaced in UI | P0 |
| MODEL-6 | Fallback chains: if primary model errors/times out, retry on a configured fallback model, event logged | P1 |
| MODEL-7 | "Test connection" button for every provider/endpoint with a canned prompt, latency + capability report | P1 |
| MODEL-8 | Embedding + reranker endpoints configurable the same way (TEI default bundled in deployment) | P0 |
| MODEL-9 | **Custom OpenAI-compatible provider wizard**: register any vendor by name + base URL + key, no code change required. Covers new inference providers (Groq, Together, Fireworks, Cerebras, etc.), aggregators, and all local servers (Ollama, vLLM, LM Studio, TGI, llama.cpp). This is the permanent escape hatch that keeps the platform vendor-future-proof | P0 |
| MODEL-10 | **Model catalog sync**: scheduled sync of LiteLLM's model/pricing/context metadata (bundled snapshot for air-gapped installs); new models under configured providers surface in Superadmin as one-click enables with pricing and context pre-filled | P1 |
| MODEL-11 | **Automated capability probing** on provider/model registration and on demand: completion, streaming, tool calling, vision, structured output, context verification. Probe results write the capability flags used for agentic gating and vision routing; declared metadata is never trusted over measured results | P0 |
| MODEL-12 | Aggregator provider templates (OpenRouter and similar): one key exposes the aggregator's full model list through the same registry, coexisting with direct BYOK | P1 |
| MODEL-13 | LiteLLM upgrade policy: pinned version, monthly bump behind a staging probe run against all registered providers before rollout | P1 |
| MODEL-14 | Two-gate model availability: Superadmin enables models platform-wide from probed providers; Org Admins allow-list from the enabled set; users see only the intersection | P0 |

### 3.5b Secrets Management (DB-backed, no .env secrets)

| ID | Requirement | Priority |
|---|---|---|
| SEC-1 | All secrets (provider API keys, SMTP, OIDC client secrets, connector tokens) stored in Postgres with envelope encryption (AES-256-GCM), managed exclusively through the Superadmin UI. The only out-of-DB secret is the Key Encryption Key, sourced from a keyfile, KMS, or Vault at bootstrap | P0 |
| SEC-2 | Write-only secret fields: never redisplayed; UI shows fingerprint (last 4 + hash) and last-used timestamp only | P0 |
| SEC-3 | KEK rotation with key_version tracking and online re-wrap; per-secret rotation with audit entries | P1 |
| SEC-4 | Secrets decrypted in a single code path, in memory, only when syncing to LiteLLM Proxy via its management API; proxy restart triggers a replay sync from DB. Secrets never appear in logs or API responses | P0 |
| SEC-5 | .env reduced to bootstrap only: DB connection + KEK source reference | P0 |

### 3.5c Token Allocation, Quotas, and Usage Reporting

| ID | Requirement | Priority |
|---|---|---|
| QUOTA-1 | Superadmin sets a monthly token allocation per organization; Admin sets per-user monthly allocations (plus a default for new users) within the org ceiling. Allocations reset on a monthly cycle with configurable reset day | P0 |
| QUOTA-2 | Per-model quota weighting: each model debits quota units at a configurable multiplier (premium API models cost more units, local models can be weighted low or zero to steer usage) | P1 |
| QUOTA-3 | Dual enforcement: pre-flight quota check in the app (cached counter, warning at threshold, block at exhaustion with reset date shown) plus mirrored per-user virtual-key budgets in LiteLLM (max_budget + 30d duration) as gateway backstop | P0 |
| QUOTA-4 | Admin top-up: one-time additional grant to a user or the org mid-cycle, audited | P1 |
| QUOTA-5 | usage_records ledger per request: user, org, model, prompt/completion tokens, quota units, latency, feature (chat, agentic, ingestion/embedding), with hourly rollups for fast reporting. Ingestion token usage attributed and visible, not hidden | P0 |
| QUOTA-6 | User-facing usage meter in chat UI: used vs allocated, reset date, always visible | P0 |
| QUOTA-7 | Admin usage dashboard: by user, model, day; top consumers; projected exhaustion date; alerts at 80% and 95% via email/webhook | P0 |
| QUOTA-8 | Superadmin reporting: cross-org rollups, currency-terms spend from catalog pricing, monthly statement per org | P1 |
| QUOTA-9 | Exports: CSV/XLSX usage reports per period for finance chargeback; scheduled monthly report email | P1 |

### 3.6 Output Rendering

| ID | Requirement | Priority |
|---|---|---|
| REND-1 | Streaming-safe markdown: GFM tables (sticky header, zebra, numeric right-align, copy CSV, download), syntax-highlighted code with copy, callouts, task lists (see markdown-renderer.tsx) | P0 |
| REND-2 | Chart fence convention (```chart JSON spec) rendered via Recharts with graceful fallback for invalid JSON; system prompt instructs models when to emit it | P1 |
| REND-3 | LaTeX/KaTeX math rendering | P2 |
| REND-4 | Mermaid diagram fence rendering | P2 |
| REND-5 | Dark/light theme, org-level branding (logo, accent color, product name) for white-label deployments | P1 |

### 3.7 Admin Console (Org Admin)

| ID | Requirement | Priority |
|---|---|---|
| ADM-1 | User management: invite, deactivate, role change, group membership | P0 |
| ADM-2 | Document library management with indexing status, storage usage, failed-job triage | P0 |
| ADM-3 | Retrieval settings per workspace: top_k, rerank threshold, agentic on/off, system prompt override, citation style | P0 |
| ADM-4 | Usage dashboard: queries/day, tokens by model, active users, top documents cited, unanswered-question log (queries that hit the no-answer path, gold for finding content gaps) | P0 |
| ADM-5 | Feedback review queue: thumbs-down answers with full trace (query, retrieved chunks, answer) for diagnosis | P1 |

### 3.8 Superadmin Console

| ID | Requirement | Priority |
|---|---|---|
| SUP-1 | Org lifecycle: create, suspend, delete (with data purge), storage/user quotas per org | P0 |
| SUP-2 | Provider + local model registration (writes LiteLLM config), global model registry | P0 |
| SUP-3 | Global budgets, cross-org usage and spend reporting | P0 |
| SUP-4 | System health: queue depth, ingestion throughput, Qdrant memory/disk, worker status, LiteLLM proxy health | P0 |
| SUP-5 | Audit log viewer with filters and export (see 3.10) | P0 |
| SUP-6 | Backup trigger + restore workflow, upgrade/migration runner | P1 |
| SUP-7 | License/instance activation if sold commercially (offline-capable license keys for air-gapped installs) | P1 if commercial |

### 3.9 Developer API and Integrations

| ID | Requirement | Priority |
|---|---|---|
| API-1 | REST API mirroring the UI: auth, documents, chat (streaming), workspaces. OpenAPI schema published | P1 |
| API-2 | Webhooks: document.indexed, document.failed, budget.threshold, chat.feedback | P2 |
| API-3 | Embeddable chat widget (script tag) scoped to a workspace with its own token | P2 |
| API-4 | MCP server exposure: workspaces as MCP tools so Claude Desktop / other agents can query the corpus | P2, high differentiation |

### 3.10 Audit and Compliance

| ID | Requirement | Priority |
|---|---|---|
| AUD-1 | Append-only audit log: logins, permission changes, key changes, document upload/delete, exports, admin setting changes, with actor, IP, timestamp | P0 |
| AUD-2 | Query logging policy per org: full (query + answer), metadata-only, or off; retention window configurable | P0 |
| AUD-3 | Data retention policies: auto-delete chats/attachments/logs after N days per org | P1 |
| AUD-4 | Erasure workflows for GDPR / India DPDP Act: delete a user's data, delete a document everywhere, produce deletion certificate entry | P1 |
| AUD-5 | PII redaction option at ingestion (regex + NER pass masking emails, phone numbers, IDs before indexing), per-workspace toggle | P2 |

### 3.11 Safety and Guardrails

| ID | Requirement | Priority |
|---|---|---|
| SAFE-1 | Prompt injection defense: retrieved chunks and attachment text wrapped in delimited data blocks with system-prompt instruction that document content is data, not instructions; injection-pattern heuristics flag suspicious chunks in the trace | P0 |
| SAFE-2 | Answer grounding: citations required; configurable strict mode where uncited claims trigger a self-check pass | P1 |
| SAFE-3 | Optional content filter hook (e.g. LlamaGuard via the same LiteLLM path) for regulated deployments | P2 |
| SAFE-4 | Rate limiting per user/IP on auth and chat endpoints; upload antivirus scan (ClamAV) | P1 |

---

## 4. Non-Functional Requirements

### 4.1 Performance targets

| Metric | Target |
|---|---|
| Vector search (hybrid, filtered, 200M+ vectors) | p95 < 150 ms |
| Rerank top-50 | p95 < 400 ms |
| First token of answer (single-shot RAG) | p95 < 2.5 s |
| First token (agentic, multi-hop) | p95 < 8 s with live activity feedback |
| Document indexing throughput | >= 50 pages/sec per worker (scalable horizontally) |
| Concurrent chat sessions per app node | 200+ (async SSE) |
| Corpus scale validated | 500 GB raw / ~250M chunks |

### 4.2 Reliability and operations
- All services stateless except Postgres, Qdrant, Redis/Dragonfly, MinIO; each with documented backup + restore (pg_dump/WAL, Qdrant snapshots, Redis persistence optional, MinIO replication).
- Health/readiness endpoints on every service; graceful degradation: if reranker is down, fall back to fusion order; if a model errors, fallback chain.
- Zero-downtime upgrades via rolling deploy; Alembic migrations forward-only with tested downgrade for last version.
- Observability: structured JSON logs, Prometheus metrics (per-stage latency: embed, search, rerank, LLM), OpenTelemetry traces across the agent loop, Grafana dashboards shipped in the deploy bundle. Langfuse (self-hosted) optional for LLM trace inspection.

### 4.3 Security
- Secrets DB-backed with envelope encryption (SEC-1 to SEC-5); the KEK is the sole bootstrap secret, sourced from keyfile/KMS/Vault. Provider keys reach LiteLLM only via in-memory sync.
- TLS everywhere; internal service mesh traffic on a private network.
- Encryption at rest: Postgres (disk-level), MinIO SSE, Qdrant on encrypted volumes.
- OWASP ASVS L2 as the review bar; dependency scanning + image scanning in CI.
- Tenant isolation tests in CI: automated suite proving org A can never retrieve org B's chunks and a user can never cite an ACL-restricted document.

### 4.4 Deployment modes
1. **Docker Compose** (single node): the 15-minute evaluation install. One `compose.yaml`, one `.env`, bundled TEI + Ollama optional profiles.
2. **Kubernetes/Helm** (production): charts for app, workers, LiteLLM proxy, Qdrant (or external), Postgres (or external), MinIO, with HPA on workers and app.
3. **Air-gapped**: fully offline bundle (images, models: bge-m3, reranker, an Ollama model), no telemetry, offline license activation. This mode is a serious differentiator for government/BFSI buyers.

---

## 5. What Businesses Will Ask For (Checklist Beyond Core RAG)

These are the questions that appear in enterprise procurement and demos; each maps to a requirement above:

1. "Can it respect our existing document permissions?" -> RBAC-5 document ACLs, SSO group mapping (the number one deal-breaker).
2. "Where does our data go when we use OpenAI?" -> BYOK + local model story, per-workspace data-residency answer.
3. "Can we prove who accessed what?" -> AUD-1/2 audit + query logging policy.
4. "What happens when the answer is wrong?" -> citations, feedback loop, no-answer honesty mode, eval harness.
5. "Can it read our SharePoint/Drive automatically?" -> DOC-4 connectors with scheduled sync.
6. "Can we control spend?" -> MODEL-5 budgets and per-org reporting.
7. "SSO or no deal." -> AUTH-2.
8. "We need it fully offline." -> air-gapped deployment mode.
9. "Can our other tools use it?" -> REST API, webhooks, MCP exposure.
10. "How do we know retrieval quality isn't degrading?" -> eval harness (golden query set with expected sources, run on every config change, scored on retrieval hit-rate and citation precision, results shown to admins).

---

## 6. Data Model (Core Entities)

```
Organization ─┬─ User ── Group
              ├─ Workspace ─┬─ Document ── Chunk(ref only; vectors in Qdrant)
              │             ├─ WorkspaceSettings (retrieval config, prompts)
              │             └─ WorkspaceMember(user|group, role)
              ├─ Chat ─┬─ Message ── Citation -> Chunk
              │        └─ Attachment (ephemeral, TTL)
              ├─ VirtualKey (LiteLLM), Budget, UsageRecord
              └─ AuditEvent (append-only)
Model (global registry: provider, capabilities, cost)
IngestJob (document or connector-sync scoped, status, error)
```

Qdrant: single collection per embedding model, payload = {tenant_id, workspace_id, document_id, acl_groups[], page, doc_type, date, ephemeral, chat_id?}. Keyword indexes on tenant_id, workspace_id, acl_groups. Every search carries must-filters for tenant + workspace membership + ACL intersection with the caller's groups.

---

## 7. Phased Roadmap

**Phase 1 - Foundation (weeks 1-6):** Auth + orgs + RBAC, upload -> ingestion -> Qdrant, single-shot hybrid RAG with streaming + citations, LiteLLM proxy with one API provider + Ollama, chat history, basic admin.

**Phase 2 - Enterprise controls (weeks 7-10):** Superadmin BYOK console with virtual keys, budgets, model registry + capability flags, document ACLs, audit log, usage dashboards, SSO (OIDC).

**Phase 3 - Agentic + polish (weeks 11-14):** Agent loop + router + activity timeline UI, reranker, semantic cache, per-chat attachments (docs + images/vision), chart rendering, exports, no-answer mode.

**Phase 4 - Scale + ecosystem (weeks 15-20):** Connectors (S3, Drive), eval harness, feedback analytics, Helm charts + air-gapped bundle, REST API + webhooks, PDF viewer with highlight, white-labeling.

---

## 8. Success Metrics

| Metric | Target (90 days post-deploy) |
|---|---|
| Answer citation coverage (answers with >= 1 valid citation) | > 95% |
| Retrieval hit rate on golden query set | > 85% top-5 |
| Thumbs-up ratio | > 80% of rated answers |
| p95 first-token latency (single-shot) | < 2.5 s |
| Weekly active users / licensed users | > 60% |
| Unanswered-question rate trending down after content-gap fixes | month-over-month decline |

---

## 9. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Embedding model change forces full re-index of 500GB | High cost/downtime | DOC-10 lock + explicit migration job with dual-read cutover |
| Local models unreliable at tool calling breaks agentic mode | Bad answers | MODEL-3 capability flags, plain-RAG fallback per model |
| Tenant/ACL leakage in retrieval | Deal-killing security incident | Filters enforced in one retrieval service code path only, isolation tests in CI, pen test before GA |
| Prompt injection via poisoned documents | Data exfiltration, wrong answers | SAFE-1 delimiting + heuristics, agent tools are read-only in v1 |
| Ingestion backlog at bulk-load time | Poor first impression | Horizontal workers, progress UI, priority queue for small interactive uploads |
| LiteLLM proxy as single point of failure | Chat outage | Run 2+ replicas behind the app, health-checked, config in shared store |
| Cost blowout on agentic queries | Budget complaints | Router gating, iteration caps, per-org budgets with hard stops |

---

## 10. Open Questions

1. Commercial model: open-core (MIT core + paid enterprise features like SSO/audit/connectors) vs fully commercial? Open-core matches the OpenAlgo playbook and drives adoption.
2. Is Postgres + pgvector acceptable as a "small deployment" profile (single container, < 5M chunks) with Qdrant as the scale profile? Reduces eval-install friction.
3. Should the eval harness ship as a CLI tool (useful standalone, marketing surface) or UI-only?
4. Default bundled local model for air-gapped installs: size/quality tradeoff to decide after tool-calling reliability testing.