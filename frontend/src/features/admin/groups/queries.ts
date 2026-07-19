import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useGroups() {
  return useQuery({
    queryKey: ['groups'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/groups');
      if (error) throw new Error('failed to load groups');
      return data;
    },
  });
}

function useGroupMutation<TInput>(fn: (input: TInput) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['groups'] }),
  });
}

export function useCreateGroup() {
  return useGroupMutation(async (name: string) => {
    const { data, error } = await api.POST('/api/v1/groups', { body: { name } });
    if (error) throw new Error('failed to create group');
    return data;
  });
}

export function useDeleteGroup() {
  return useGroupMutation(async (groupId: string) => {
    const { error } = await api.DELETE('/api/v1/groups/{group_id}', {
      params: { path: { group_id: groupId } },
    });
    if (error) throw new Error('failed to delete group');
  });
}

export function useSetGroupMembership() {
  return useGroupMutation(
    async (input: { groupId: string; userId: string; member: boolean }) => {
      const params = {
        path: { group_id: input.groupId, user_id: input.userId },
      };
      const { error } = input.member
        ? await api.PUT('/api/v1/groups/{group_id}/members/{user_id}', { params })
        : await api.DELETE('/api/v1/groups/{group_id}/members/{user_id}', { params });
      if (error) throw new Error('failed to update membership');
    },
  );
}
