// The ONLY file allowed to reach into components['schemas']. If a generated name
// differs from an assumption, fix the alias here — nowhere else.
import type { components } from './schema';

export type UserOut = components['schemas']['UserOut'];
export type WorkspaceOut = components['schemas']['WorkspaceOut'];
export type DocumentOut = components['schemas']['DocumentOut'];
export type ChatOut = components['schemas']['ChatOut'];
// GET /chats/{chat_id} is a NESTED tree (chat/schemas.py): roots in
// ChatTreeOut.messages, recursion via MessageNode.children (sorted by sibling_index).
export type ChatTreeOut = components['schemas']['ChatTreeOut'];
export type MessageNode = components['schemas']['MessageNode'];
export type CitationOut = components['schemas']['CitationOut'];
// Thumbs up/down + optional comment on an assistant answer (CHAT-10). Present
// on MessageNode.feedback for the caller's own rating.
export type FeedbackOut = components['schemas']['FeedbackOut'];
export type ModelOut = components['schemas']['ModelOut'];
// POST /api/v1/admin/models wire shape (DOC-10). features/admin/models/queries.ts
// defines its own ergonomic ModelCreate (modality/dimension optional --
// the backend defaults modality to "chat" when the key is omitted, so
// pre-DOC-10 callers stay byte-identical; provider_kind narrower, excluding
// the seed-only 'tei') and casts to this alias at the request boundary.
export type ModelCreateWire = components['schemas']['ModelCreate'];
// GET /api/v1/models (non-admin, "any authenticated user") returns this
// slimmer shape — id + display_name only, already filtered to enabled models
// server-side. ModelOut (id/display_name/enabled/…) is the admin-page shape.
export type ModelPublic = components['schemas']['ModelPublic'];
// GET /api/v1/admin/usage/summary (QUOTA-1 + ADM-4, extended by Plan J Task
// 4): KPI tiles, the two per-day series (queries_per_day,
// tokens_by_model_per_day), top users, the Auditor rollup (answer_quality)
// and the worst-scoring answers table (worst_answers). Composed route-locally
// in usage.py from UsageSummaryOut (quotas/schemas.py) — that base schema is
// inlined by FastAPI/pydantic's schema generation and so no longer appears
// as its own component now that DashboardSummaryOut is the only response
// model referencing it; DashboardSummaryOut is the one true alias here.
export type DashboardSummaryOut = components['schemas']['DashboardSummaryOut'];
export type AnswerQualityOut = components['schemas']['AnswerQualityOut'];
export type WorstAnswerOut = components['schemas']['WorstAnswerOut'];
// Task 12 (Plan J, §6): the latest EvalRun per workspace, for the dashboard's
// eval-trend table (worst_answers' sibling in DashboardSummaryOut).
export type EvalTrendOut = components['schemas']['EvalTrendOut'];
// GET /api/v1/superadmin/client-errors (Task 6). message/stack/url are
// attacker-controlled — render as text only, never dangerouslySetInnerHTML.
export type ClientErrorOut = components['schemas']['ClientErrorOut'];
// GET /api/v1/admin/models/catalog (MODEL-10/G7): LiteLLM's known
// name+provider+pricing catalog, cross-referenced against the registry.
// Cost fields are null when unknown to LiteLLM — omit pricing, don't show $0.
export type CatalogEntryOut = components['schemas']['CatalogEntryOut'];
export type CatalogOut = components['schemas']['CatalogOut'];
// Per-workspace metadata schema (DOC-6, Task 9/11): field_type is
// 'text' | 'date' | 'select'; options is populated only for 'select'.
export type MetadataFieldOut = components['schemas']['MetadataFieldOut'];
// GET/POST /api/v1/admin/roles (Task 12/14): superadmin-authored custom role
// templates. `permissions` is a flat list of dotted flags (see
// PERMISSION_LABELS in features/admin/roles/role-form-dialog.tsx).
export type RoleTemplateOut = components['schemas']['RoleTemplateOut'];
// GET/POST /api/v1/workspaces/{workspace_id}/golden-queries, DELETE
// /api/v1/golden-queries/{query_id} (Plan J Task 9/10): admin-authored
// eval fixtures — a question plus the document ids retrieval should hit.
export type GoldenQueryOut = components['schemas']['GoldenQueryOut'];
export type GoldenQueryCreate = components['schemas']['GoldenQueryCreate'];
// GET/PUT /api/v1/admin/orgs/{org_id}/quota (QUOTA-1, K-C11): superadmin-only
// org-wide monthly allocation + default per-user allocation + reset day.
export type OrgQuotaOut = components['schemas']['OrgQuotaOut'];
export type OrgQuotaIn = components['schemas']['OrgQuotaIn'];
// GET /api/v1/users/{user_id}/quota (Task 15): the target user's override
// (null = using the org default) plus their current-period usage, in one call.
export type UserQuotaOut = components['schemas']['UserQuotaOut'];
// GET/POST /api/v1/workspaces/{workspace_id}/folders, PATCH/DELETE
// /api/v1/folders/{folder_id} (Plan H, folder management Tasks 1-2):
// workspace-scoped folder tree node. parent_folder_id is null for roots.
export type FolderOut = components['schemas']['FolderOut'];

export type DocumentStatus = DocumentOut['status'];

// --- SSE payloads (outside OpenAPI) ---
// Mirrors backend/src/raghub/modules/chat/events.py — the authoritative wire
// contract. `marker` is the [n] number cited in the answer text.

export interface SourceRef {
  marker: number;
  document_id: string;
  filename: string;
  page: number;
  chunk_index: number;
  score: number;
  snippet: string;
  section: string | null;
  version: number;
  // Phase 3 Plan I Task 11 (D7): set for web-search hits only; document_id
  // stays "" (not null) for those so wire strings never go null.
  url: string | null;
}

export interface CitationRef {
  marker: number;
  document_id: string;
  chunk_ref: string;
  page: number;
  score: number;
  section: string | null;
  version: number;
  url: string | null;
}

export interface DoneInfo {
  message_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  no_answer: boolean;
  grounding: 'documents' | 'general';
  // Phase 3 Plan J (design D4/§3): true only when the Gatekeeper's second
  // (critique-guided retry) attempt is what streamed — win or lose, it was
  // never re-verified by a second judge call.
  validation_failed: boolean;
}

// Phase 3 (design §2, Task 10): one frame per agent tool execution on an
// escalated turn. Additive — absent entirely on non-escalated turns.
export interface AgentStepInfo {
  n: number;
  tool: string;
  query: string;
}
