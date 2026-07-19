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
}

export interface CitationRef {
  marker: number;
  document_id: string;
  chunk_ref: string;
  page: number;
  score: number;
}

export interface DoneInfo {
  message_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  no_answer: boolean;
}
