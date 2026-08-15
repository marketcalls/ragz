import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useUsageSummary(days: number) {
  return useQuery({
    queryKey: ['admin-usage', days],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/usage/summary', {
        params: { query: { days } },
      });
      if (error) throw new Error('failed to load usage summary');
      return data;
    },
  });
}

// GET /api/v1/admin/usage/orgs is superadmin-only (require_role('superadmin')
// -- see policy.py), unlike /admin/usage/summary which is gated by the
// broader 'analytics.view' action. `enabled` should be wired to the caller's
// own role check so plain admins viewing this dashboard never issue a
// request that's guaranteed to 403.
export function useOrgUsage(enabled: boolean) {
  return useQuery({
    queryKey: ['admin-usage-orgs'],
    enabled,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/usage/orgs');
      if (error) throw new Error('failed to load org usage');
      return data;
    },
  });
}
