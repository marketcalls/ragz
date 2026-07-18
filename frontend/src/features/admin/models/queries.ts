import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export interface ModelCreate {
  display_name: string;
  litellm_model_name: string;
  provider_kind: 'openai' | 'ollama' | 'openai_compatible';
  base_url?: string;
  api_key?: string; // write-only: sent, never read back
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
      const { data, error } = await api.POST('/api/v1/admin/models', { body });
      if (error) throw new Error('failed to add model');
      return data;
    },
    onSuccess: invalidate,
  });
}

export function usePatchModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (input: { modelId: string; body: { enabled?: boolean } }) => {
      const { data, error } = await api.PATCH('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: input.modelId } },
        body: input.body,
      });
      if (error) throw new Error('failed to update model');
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (modelId: string) => {
      const { error } = await api.DELETE('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: modelId } },
      });
      if (error) throw new Error('failed to remove model');
    },
    onSuccess: invalidate,
  });
}
