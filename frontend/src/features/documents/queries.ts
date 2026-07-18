import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

// DEVIATION (Task 10): the Documents feature (Task 12) hadn't landed yet, but
// chat-page.tsx needs the workspace's document list to resolve citation
// filenames. Added the minimal read hook here rather than block; the
// Documents page task should reuse this query rather than duplicate it.
export function useDocuments(workspaceId: string | null) {
  return useQuery({
    queryKey: ['documents', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/documents', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load documents');
      return data;
    },
  });
}
