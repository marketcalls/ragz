import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useWorkspaces() {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces');
      if (error) throw new Error('failed to load workspaces');
      return data;
    },
  });
}

export function usePatchWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      ...body
    }: {
      id: string;
      top_k?: number;
      min_score?: number;
      rerank_enabled?: boolean;
      system_prompt_override?: string | null;
      fallback_policy?: 'general_knowledge' | 'decline';
      web_search_enabled?: boolean;
      strict_mode?: boolean;
      enrichment_enabled?: boolean;
    }) => {
      const { data, error } = await api.PATCH('/api/v1/workspaces/{workspace_id}', {
        params: { path: { workspace_id: id } },
        body,
      });
      if (error) throw new Error('failed to update workspace settings');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string }) => {
      const { data, error } = await api.POST('/api/v1/workspaces', { body });
      if (error) throw new Error('failed to create workspace');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export function usePatchEmbeddingModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, embedding_model_id }: { id: string; embedding_model_id: string }) => {
      const { data, error, response } = await api.PATCH(
        '/api/v1/workspaces/{workspace_id}/embedding-model',
        { params: { path: { workspace_id: id } }, body: { embedding_model_id } },
      );
      if (error) {
        if (response?.status === 409) {
          throw new EmbeddingModelLockedError('workspace already has indexed documents');
        }
        throw new Error('failed to update embedding model');
      }
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export class EmbeddingModelLockedError extends Error {}

export function useStartReembed() {
  return useMutation({
    mutationFn: async ({ id, new_embedding_model_id }: { id: string; new_embedding_model_id: string }) => {
      const { data, error } = await api.POST('/api/v1/workspaces/{workspace_id}/reembed', {
        params: { path: { workspace_id: id } },
        body: { new_embedding_model_id },
      });
      if (error) throw new Error('failed to start re-embed');
      return data;
    },
  });
}

export function useReembedStatus(workspaceId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['reembed-status', workspaceId],
    enabled,
    refetchInterval: (query) => (query.state.data?.finished_at ? false : 1500),
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        '/api/v1/workspaces/{workspace_id}/reembed-status',
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        if (response?.status === 404) return null;
        throw new Error('failed to load re-embed status');
      }
      return data;
    },
  });
}
