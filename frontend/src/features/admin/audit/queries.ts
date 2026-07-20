import { useInfiniteQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export type AuditFilters = {
  action?: string;
  actor_id?: string;
  org_id?: string;
  date_from?: string;
  date_to?: string;
};

// A bare `date_to: 'YYYY-MM-DD'` is a calendar-day picker value, but
// list_audit_events's bound is an exact `<=` timestamp comparison -- left
// untouched, it would exclude every event from later in that day. Widened
// here (not in the backend) so other callers of list_audit_events keep the
// exact bound they asked for.
function widenDateTo(filters: AuditFilters): AuditFilters {
  if (!filters.date_to) return filters;
  return { ...filters, date_to: `${filters.date_to}T23:59:59.999` };
}

export function useAuditLog(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: ['audit', filters],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/api/v1/admin/audit', {
        params: {
          query: {
            ...Object.fromEntries(Object.entries(widenDateTo(filters)).filter(([, v]) => v)),
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
