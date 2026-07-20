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
      // openapi-fetch can resolve `error` as a falsy value (e.g. `""`) when
      // the server (or a dev proxy) sends a malformed non-JSON error body --
      // `if (error)` alone misses that case and would let `data` (also
      // undefined) flow back as this page's data. useInfiniteQuery has no
      // "data cannot be undefined" rescue the way plain useQuery does, so an
      // undefined page here throws inside getNextPageParam instead of
      // surfacing a normal isError state. Guard on `!data` too so the
      // queryFn itself always throws a real Error and never returns
      // undefined, regardless of that internal React Query behavior.
      if (error || !data) throw new Error('failed to load audit log');
      return data;
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
