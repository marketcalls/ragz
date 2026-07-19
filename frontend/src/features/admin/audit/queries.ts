import { useInfiniteQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export type AuditFilters = {
  action?: string;
  actor_id?: string;
  org_id?: string;
  date_from?: string;
  date_to?: string;
};

export function useAuditLog(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: ['audit', filters],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/api/v1/admin/audit', {
        params: {
          query: {
            ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
            cursor: pageParam,
            limit: 50,
          },
        },
      });
      if (error) throw new Error('failed to load audit log');
      return data;
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
