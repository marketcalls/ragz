import { useInfiniteQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export type FeedbackFilters = {
  rating?: string;
  workspace_id?: string;
  user_id?: string;
  start?: string;
  end?: string;
};

export function useFeedbackQueue(filters: FeedbackFilters) {
  return useInfiniteQuery({
    queryKey: ['admin-feedback', filters],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/api/v1/admin/feedback', {
        params: {
          query: {
            ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
            cursor: pageParam,
            limit: 50,
          },
        },
      });
      // See useAuditLog's identical guard: useInfiniteQuery has no "data
      // cannot be undefined" rescue the way plain useQuery does, so guard on
      // `!data` too, not just `if (error)`.
      if (error || !data) throw new Error('failed to load feedback queue');
      return data;
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
