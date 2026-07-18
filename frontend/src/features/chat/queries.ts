import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useChats(workspaceId: string | null) {
  return useQuery({
    queryKey: ['chats', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      // workspace_id is optional server-side (omitted = all the caller's chats);
      // the sidebar always scopes to the active workspace.
      const { data, error } = await api.GET('/api/v1/chats', {
        params: { query: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load chats');
      return data;
    },
  });
}
