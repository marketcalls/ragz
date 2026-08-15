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
// Transcript rendering (design 2026-08-15): metadata-only view of a
// ChatAttachment sent alongside a MessageNode -- id/kind/filename/mime/
// status, never bytes/storage_key/extracted_text.
export type AttachmentOut = components['schemas']['AttachmentOut'];
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
// GET /api/v1/usage/me (QUOTA-1): the caller's own current-period usage.
// allocated_tokens is null when no allocation is configured (unlimited/local) --
// see features/chat/usage-meter.tsx (the existing chat-header widget) and
// features/usage/usage-page.tsx (the "My Usage" page) for the two consumers.
export type UsageMeterOut = components['schemas']['UsageMeterOut'];
// GET /api/v1/usage/me/daily: per-day token totals (prompt vs completion) for
// the current user over a 7/30/90-day window, zero-filled + ascending. Powers
// the "Usage over time" stacked-area chart on the "My Usage" page.
export type DailyUsagePointOut = components['schemas']['DailyUsagePointOut'];
// GET /api/v1/users/{user_id}/quota (Task 15): the target user's override
// (null = using the org default) plus their current-period usage, in one call.
export type UserQuotaOut = components['schemas']['UserQuotaOut'];
// GET/POST /api/v1/workspaces/{workspace_id}/folders, PATCH/DELETE
// /api/v1/folders/{folder_id} (Plan H, folder management Tasks 1-2):
// workspace-scoped folder tree node. parent_folder_id is null for roots.
export type FolderOut = components['schemas']['FolderOut'];
// GET /api/v1/folders/{folder_id}/delete-preview: read-only document/subfolder
// counts for a folder's subtree, fetched to back the delete confirmation
// dialog before the actual (irreversible) DELETE call.
export type FolderDeletePreview = components['schemas']['FolderDeletePreview'];
// GET/PUT /api/v1/admin/settings (Tasks 2-3, pluggable parser + reranker):
// superadmin-only document-parser + reranker-provider selection. `*_key_set`
// booleans report whether an encrypted key already exists — the keys
// themselves are write-only and never appear in ProviderSettingsOut.
export type ProviderSettingsOut = components['schemas']['ProviderSettingsOut'];
export type ProviderSettingsUpdate = components['schemas']['ProviderSettingsUpdate'];
// GET /api/v1/admin/api-keys (Task 6): masked listing -- NO api_key/key_hash
// field (iron rule 3). acl/tenant scoping for the external API path lives
// entirely server-side via the key's user_id + workspace_id.
export type ApiKeyOut = components['schemas']['ApiKeyOut'];
// POST /api/v1/admin/api-keys response: ApiKeyOut plus the raw `api_key`,
// present ONLY on this one response. Never persisted to any query cache
// beyond the mutation's own transient result -- see api-keys/queries.ts.
export type ApiKeyCreatedOut = components['schemas']['ApiKeyCreatedOut'];

// GET/POST/PATCH /api/v1/admin/bots (Task 8): chat-platform bot integrations
// (Telegram/Discord/Slack). No credential field -- iron rule 3: `token`/
// `signing_secret` cross the boundary only on BotIntegrationCreate (the
// superadmin's own paste), never back out. `webhook_url` is derived
// server-side from `webhook_id` + platform for pasting into the platform's
// own webhook settings.
export type BotIntegrationOut = components['schemas']['BotIntegrationOut'];

// In-chat generative-UI blocks (Phase 2 design doc, chat/blocks.py): a
// server-validated (Iron Rule 5), bounded, discriminated-on-`type` union.
// Delivered two ways -- both carry the same shape and both feed the ONE
// whitelist BlockRenderer (features/chat/blocks/block-renderer.tsx):
//   (a) LIVE: the `blocks` SSE frame during a chat turn (see stream.ts).
//   (b) PERSISTED: MessageNode.blocks from chat history GET.
export type TextBlock = components['schemas']['TextBlock'];
export type ChartBlock = components['schemas']['ChartBlock'];
export type InfoCardBlock = components['schemas']['InfoCardBlock'];
export type ImageCardBlock = components['schemas']['ImageCardBlock'];
export type RankedListBlock = components['schemas']['RankedListBlock'];
export type TagBadgesBlock = components['schemas']['TagBadgesBlock'];
export type CalloutBlock = components['schemas']['CalloutBlock'];
export type TableBlock = components['schemas']['TableBlock'];
export type TabsBlock = components['schemas']['TabsBlock'];
export type TabItem = components['schemas']['TabItem'];
// Phase 3 (interactive in-chat forms, 2026-08-15 design): a `form` block --
// the model asks for structured input; submission is a NORMAL chat message
// composed client-side (features/chat/blocks/form-block.tsx), never a new
// endpoint or stateful form engine. Allowed at top level and inside
// TabItem.blocks.
export type FormBlock = components['schemas']['FormBlock'];
export type FormField = components['schemas']['FormField'];
export type Block =
  | TextBlock
  | ChartBlock
  | InfoCardBlock
  | ImageCardBlock
  | RankedListBlock
  | TagBadgesBlock
  | CalloutBlock
  | TableBlock
  | TabsBlock
  | FormBlock;

export type DocumentStatus = DocumentOut['status'];

// --- SSE payloads (outside OpenAPI) ---
// Mirrors backend/src/ragz/modules/chat/events.py — the authoritative wire
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

// Design 2026-08-15 ("Behind the scenes" UI): emitted right after a
// successful web_search AgentStepInfo, same `n`, carrying the results
// already surfaced as `sources`/`citations` -- this frame just reshapes them
// for the expandable web_search result card. Display-only, live-turn-only
// (not persisted): a page reload shows no "Behind the scenes" section.
export interface ToolResultItem {
  title: string;
  url: string;
  source: string;
  snippet: string;
}

export interface ToolResultInfo {
  n: number;
  tool: string;
  results: ToolResultItem[];
}
