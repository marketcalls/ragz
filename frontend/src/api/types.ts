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
// GET /api/v1/admin/usage/summary (QUOTA-1 + ADM-4): KPI tiles, the two
// per-day series (queries_per_day, tokens_by_model_per_day), and top users.
export type UsageSummaryOut = components['schemas']['UsageSummaryOut'];
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
}

export interface CitationRef {
  marker: number;
  document_id: string;
  chunk_ref: string;
  page: number;
  score: number;
  section: string | null;
  version: number;
}

export interface DoneInfo {
  message_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  no_answer: boolean;
}
