import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { ModelOut } from '@/api/types';

export interface ModelCreate {
  display_name: string;
  litellm_model_name: string;
  provider_kind: 'openai' | 'ollama' | 'openai_compatible';
  base_url?: string;
  api_key?: string; // write-only: sent, never read back
}

export interface ModelPatchInput {
  display_name?: string;
  base_url?: string;
  enabled?: boolean;
  api_key?: string; // write-only: sent, never read back
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
