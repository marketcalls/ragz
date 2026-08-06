import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { ApiKeyCreatedOut, ApiKeyOut } from '@/api/types';

// GET/POST/DELETE /api/v1/admin/api-keys (Task 6). Superadmin-only external
// API key management -- iron rule 3: the raw key crosses the wire exactly
// once, on the POST response (ApiKeyCreatedOut). The list/GET response
// (ApiKeyOut) is masked -- only `prefix`, no key/hash -- and that's the only
// shape this query key ever caches; the created-key mutation result is never
// written into it (see api-keys-page.tsx: the dialog reads it straight off
// `useCreateApiKey().data` and drops it via `.reset()` on close).
const KEY = ['admin', 'api-keys'];

export interface ApiKeyCreateInput {
  name: string;
  user_id: string;
  workspace_id: string;
  expires_at?: string | null;
}

export function useApiKeys() {
  return useQuery({
    queryKey: KEY,
    queryFn: async (): Promise<ApiKeyOut[]> => {
      const { data, error } = await api.GET('/api/v1/admin/api-keys');
      if (error) throw new Error('failed to load API keys');
      return data;
    },
  });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ApiKeyCreateInput): Promise<ApiKeyCreatedOut> => {
      const { data, error } = await api.POST('/api/v1/admin/api-keys', { body });
      if (error) throw new Error('failed to generate API key');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (keyId: string) => {
      const { error } = await api.DELETE('/api/v1/admin/api-keys/{key_id}', {
        params: { path: { key_id: keyId } },
      });
      if (error) throw new Error('failed to revoke API key');
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
