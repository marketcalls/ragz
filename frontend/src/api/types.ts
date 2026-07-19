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
export type ModelOut = components['schemas']['ModelOut'];
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
