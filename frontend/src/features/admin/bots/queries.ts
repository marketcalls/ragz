import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { BotIntegrationOut } from '@/api/types';

// GET/POST/PATCH/DELETE /api/v1/admin/bots (Task 8). Superadmin-only
// chat-platform bot management -- iron rule 3: token/signing_secret are
// write-only on the create body; BotIntegrationOut never carries a
// credential field, so unlike API keys there is no "shown once" response
// to hold in local state at all.
const KEY = ['admin', 'bots'];

export interface BotIntegrationCreateInput {
  platform: 'telegram' | 'discord' | 'slack';
  name: string;
  workspace_id: string;
  user_id: string;
  token: string;
  signing_secret: string;
}

export function useBots() {
  return useQuery({
    queryKey: KEY,
    queryFn: async (): Promise<BotIntegrationOut[]> => {
      const { data, error } = await api.GET('/api/v1/admin/bots');
      if (error) throw new Error('failed to load bot integrations');
      return data;
    },
  });
}

export function useCreateBot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: BotIntegrationCreateInput): Promise<BotIntegrationOut> => {
      const { data, error } = await api.POST('/api/v1/admin/bots', { body });
      if (error) throw new Error('failed to create bot integration');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

export function useSetBotEnabled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      const { error } = await api.PATCH('/api/v1/admin/bots/{bot_id}', {
        params: { path: { bot_id: id } },
        body: { enabled },
      });
      if (error) throw new Error('failed to update bot integration');
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteBot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.DELETE('/api/v1/admin/bots/{bot_id}', {
        params: { path: { bot_id: id } },
      });
      if (error) throw new Error('failed to remove bot integration');
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
