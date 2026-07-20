import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { OrgQuotaIn } from '@/api/types';

// GET/PUT /api/v1/admin/orgs/{org_id}/quota (QUOTA-1, K-C11 backend; Task 15
// wires the superadmin form). `enabled` gates the fetch so the dialog can be
// mounted closed (orgId '') without firing a request, same shape as
// admin/users/queries.ts's useUserQuota.
export function useOrgQuota(orgId: string, enabled = true) {
  return useQuery({
    queryKey: ['org-quota', orgId],
    enabled: enabled && orgId !== '',
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/orgs/{org_id}/quota', {
        params: { path: { org_id: orgId } },
      });
      if (error) throw new Error('failed to load org quota');
      return data;
    },
  });
}

export function usePutOrgQuota(orgId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: OrgQuotaIn) => {
      const { data, error } = await api.PUT('/api/v1/admin/orgs/{org_id}/quota', {
        params: { path: { org_id: orgId } },
        body,
      });
      if (error) throw new Error('failed to save org quota');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['org-quota', orgId] }),
  });
}
