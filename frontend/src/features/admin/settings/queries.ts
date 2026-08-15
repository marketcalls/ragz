import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

// GET/PUT /api/v1/admin/settings (Tasks 2-3, pluggable parser + reranker).
// The generated ProviderSettingsOut types document_parser/rerank_provider/
// cohere_rerank_model as bare `string` (the backend's Out schema has no
// Literal, unlike ProviderSettingsUpdate) — narrowed here the same way
// admin/health/queries.ts narrows its untyped-response shapes, since the
// route only ever emits one of these values.
export type CohereRerankModel = 'rerank-v4.0-fast' | 'rerank-v4.0-pro';

export interface ProviderSettings {
  document_parser: 'anydoc' | 'docling' | 'llamaparse' | 'liteparse';
  rerank_provider: 'local' | 'cohere';
  cohere_rerank_model: CohereRerankModel;
  llamaparse_key_set: boolean;
  cohere_key_set: boolean;
}

export interface ProviderSettingsUpdate {
  document_parser?: 'anydoc' | 'docling' | 'llamaparse' | 'liteparse';
  rerank_provider?: 'local' | 'cohere';
  cohere_rerank_model?: CohereRerankModel;
  llamaparse_api_key?: string;
  cohere_api_key?: string;
}

const KEY = ['admin', 'settings'];

export function useProviderSettings() {
  return useQuery({
    queryKey: KEY,
    queryFn: async (): Promise<ProviderSettings> => {
      const { data, error } = await api.GET('/api/v1/admin/settings');
      if (error) throw new Error('failed to load settings');
      return data as unknown as ProviderSettings;
    },
  });
}

export function useUpdateProviderSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (patch: ProviderSettingsUpdate) => {
      const { data, error } = await api.PUT('/api/v1/admin/settings', { body: patch });
      if (error) throw new Error('failed to save settings');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
