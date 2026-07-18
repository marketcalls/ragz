import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { DocumentOut } from '@/api/types';

import { shouldPoll } from './status';

export function useDocuments(workspaceId: string | null) {
  return useQuery({
    queryKey: ['documents', workspaceId],
    enabled: workspaceId !== null,
    refetchInterval: (query) =>
      shouldPoll(query.state.data as DocumentOut[] | undefined) ? 2500 : false,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/documents', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load documents');
      return data;
    },
  });
}

export function useDeleteDocument(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const { error } = await api.DELETE('/api/v1/documents/{document_id}', {
        params: { path: { document_id: documentId } },
      });
      if (error) throw new Error('failed to delete document');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] }),
  });
}
