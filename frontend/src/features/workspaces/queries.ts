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
