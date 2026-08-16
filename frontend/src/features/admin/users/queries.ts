import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useUsers() {
  return useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/users');
      if (error) throw new Error('failed to load users');
      return data;
    },
  });
}

export function usePatchUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      userId: string;
      body: { active?: boolean; role?: 'admin' | 'user' };
    }) => {
      const { data, error } = await api.PATCH('/api/v1/users/{user_id}', {
        params: { path: { user_id: input.userId } },
        body: input.body,
      });
      if (error) throw new Error('failed to update user');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useInvite() {
  return useMutation({
    mutationFn: async (body: { email: string; role: 'admin' | 'user'; org_id?: string }) => {
      const { data, error } = await api.POST('/api/v1/auth/invitations', { body });
      if (error) throw new Error('failed to create invitation');
      return data;
    },
  });
}

// GET/PUT /api/v1/users/{user_id}/quota (Task 15): the per-user override
// editor's data source. `enabled` gates the fetch so the dialog can be
// mounted closed (no target row selected) without firing a request.
export function useUserQuota(userId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['user-quota', userId],
    enabled: enabled && userId !== '',
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/users/{user_id}/quota', {
        params: { path: { user_id: userId } },
      });
      if (error) throw new Error('failed to load user quota');
      return data;
    },
  });
}

export function useSetUserQuota() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      userId,
      monthlyTokens,
    }: {
      userId: string;
      monthlyTokens: number | null;
    }) => {
      const { error } = await api.PUT('/api/v1/users/{user_id}/quota', {
        params: { path: { user_id: userId } },
        body: { monthly_tokens: monthlyTokens },
      });
      if (error) throw new Error('failed to save user quota');
    },
    onSuccess: (_, { userId }) =>
      void queryClient.invalidateQueries({ queryKey: ['user-quota', userId] }),
  });
}
