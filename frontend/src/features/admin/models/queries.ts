import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { CatalogOut, ModelOut } from '@/api/types';

export interface ModelCreate {
  display_name: string;
  litellm_model_name: string;
  // 'litellm' = any LiteLLM-native provider (anthropic, gemini, groq, …):
  // the model name is passed to the gateway verbatim, no base_url needed.
  provider_kind: 'openai' | 'ollama' | 'openai_compatible' | 'litellm';
  base_url?: string;
  api_key?: string; // write-only: sent, never read back
  // Phase 3 Plan I (MODEL-3): flag models that silently emit tool-call JSON
  // as content instead of calling tools natively; the agent loop then uses
  // the JSON-planner protocol for them. Required (not optional): the
  // generated wire type has no default-implies-optional exemption, so every
  // caller states it explicitly.
  tools_unreliable: boolean;
}

export interface ModelPatchInput {
  display_name?: string;
  base_url?: string;
  enabled?: boolean;
  api_key?: string; // write-only: sent, never read back
  tools_unreliable?: boolean;
  // Phase 3 Plan J (D5/§4): setting true designates this model as THE utility
  // model, clearing every other row's flag server-side in the same
  // transaction — callers only ever send `true`, never `false` (the backend
  // owns exclusivity; see models-page.tsx's radio).
  is_utility?: boolean;
}

// A 502 on these routes means the LOCAL write already succeeded and only the
// LiteLLM gateway sync failed — the row is persisted with sync_status="error".
// Callers surface this as a distinguishable, partial-success error rather
// than a generic failure, and must still refresh the list either way.
export class PartialSyncError extends Error {}

function mutationError(response: Response | undefined): Error {
  if (response?.status === 502) {
    return new PartialSyncError('saved locally — gateway sync failed; see sync status');
  }
  return new Error('request failed');
}

// Mutations now return before the background LiteLLM replay completes, so a
// created/patched/deleted row can sit at sync_status="pending" for a beat.
// Poll while any row is still pending so the eventual "synced"/"error"
// outcome becomes visible without a manual refresh (mirrors the documents
// feature's shouldPoll pattern in features/documents/queries.ts, via
// features/documents/status.ts).
export function adminModelsRefetchInterval(models: ModelOut[] | undefined): number | false {
  return (models ?? []).some((m) => m.sync_status === 'pending') ? 2000 : false;
}

function useInvalidateModels() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-models'] });
    void queryClient.invalidateQueries({ queryKey: ['models'] });
  };
}

export function useAdminModels() {
  return useQuery({
    queryKey: ['admin-models'],
    refetchInterval: (query) =>
      adminModelsRefetchInterval(query.state.data as ModelOut[] | undefined),
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/models');
      if (error) throw new Error('failed to load models');
      return data;
    },
  });
}

// MODEL-10/G7: LiteLLM's catalog (context window + pricing) cross-referenced
// against the registry. `enabled` defaults to true — callers pass false to
// skip the fetch until their own mount point is ready (e.g. a closed dialog
// that shares this query key with the always-mounted page). staleTime keeps
// repeat opens of the browse dialog from re-fetching the same list.
export function useCatalog(enabled = true) {
  return useQuery({
    queryKey: ['admin-models-catalog'],
    enabled,
    staleTime: 5 * 60_000,
    queryFn: async (): Promise<CatalogOut> => {
      const { data, error } = await api.GET('/api/v1/admin/models/catalog');
      if (error) throw new Error('failed to load model catalog');
      return data;
    },
  });
}

export function useCreateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (body: ModelCreate) => {
      const { data, error, response } = await api.POST('/api/v1/admin/models', { body });
      if (error) throw mutationError(response);
      return data;
    },
    // Invalidate on every outcome, not just success: a 502 still means the
    // local row changed (server state moved), so the stale cache must go.
    onSettled: invalidate,
  });
}

export function usePatchModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (input: { modelId: string; body: ModelPatchInput }) => {
      const { data, error, response } = await api.PATCH('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: input.modelId } },
        body: input.body,
      });
      if (error) throw mutationError(response);
      return data;
    },
    onSettled: invalidate,
  });
}

export function useDeleteModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (modelId: string) => {
      const { error, response } = await api.DELETE('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: modelId } },
      });
      if (error) throw mutationError(response);
    },
    onSettled: invalidate,
  });
}
