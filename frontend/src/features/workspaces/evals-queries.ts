import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/features/auth/mutations';

// Task 10 (eval harness, design §6): mirrors documents/queries.ts's
// metadata-field hooks — one query keyed by workspace, two mutations that
// invalidate it. GoldenQueryOut/GoldenQueryCreate come straight off the
// generated schema (backend/src/ragz/modules/evals/schemas.py) — no
// hand-rolled duplicate types at this boundary.
export function useGoldenQueries(workspaceId: string | null) {
  return useQuery({
    queryKey: ['golden-queries', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/golden-queries', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load golden queries');
      return data;
    },
  });
}

export function useCreateGoldenQuery(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { question: string; expected_document_ids: string[] }) => {
      const { data, error } = await api.POST('/api/v1/workspaces/{workspace_id}/golden-queries', {
        params: { path: { workspace_id: workspaceId as string } },
        body: input,
      });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['golden-queries', workspaceId] }),
  });
}

export function useDeleteGoldenQuery(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (queryId: string) => {
      const { error } = await api.DELETE('/api/v1/golden-queries/{query_id}', {
        params: { path: { query_id: queryId } },
      });
      if (error) throw new Error('failed to delete golden query');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['golden-queries', workspaceId] }),
  });
}
