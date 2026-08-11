import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export interface Authorization {
  role: 'superadmin' | 'admin' | 'user';
  permissions: Set<string>;
  policyVersion: number | null;
}

// Effective, server-computed authorization for the calling user (role +
// resolved custom-role permissions). UI convenience only -- backend
// require_action/require_role dependencies remain the real security
// boundary (RBAC-12).
export function useAuthorization() {
  return useQuery({
    queryKey: ['me', 'authorization'],
    queryFn: async (): Promise<Authorization> => {
      const { data, error } = await api.GET('/api/v1/me/authorization');
      if (error) throw new Error('failed to load authorization');
      return {
        role: data.role as Authorization['role'],
        permissions: new Set(data.permissions),
        policyVersion: data.policy_version,
      };
    },
    staleTime: 60_000,
  });
}
