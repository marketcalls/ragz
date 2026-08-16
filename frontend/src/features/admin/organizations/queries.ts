import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { OrgOut } from '@/api/types';
import { problemDetail } from '@/features/auth/mutations';

export type Organization = OrgOut;

// The optional profile fields shared by OrgCreate/OrgUpdate.
export interface OrgProfileFields {
  contact_email?: string | null;
  industry?: string | null;
  company_size?: string | null;
  country?: string | null;
}

// GET/POST/PATCH /api/v1/admin/orgs (M1): superadmin-only org list/create/rename.
// Shares the ['admin', 'orgs'] query key with features/admin/sso/queries.ts's
// useAdminOrgs -- same endpoint, same cache entry, so a create/rename here is
// immediately reflected on the SSO page's org list (and vice versa).
const ORGS_KEY = ['admin', 'orgs'];

// `enabled` lets non-superadmin callers (e.g. InviteDialog, mounted for any
// admin) skip the request entirely — GET /api/v1/admin/orgs is
// superadmin-only and would 403 for everyone else.
export function useOrganizations(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ORGS_KEY,
    enabled: options?.enabled ?? true,
    queryFn: async (): Promise<Organization[]> => {
      const { data, error } = await api.GET('/api/v1/admin/orgs');
      if (error) throw new Error('failed to load organizations');
      return data;
    },
  });
}

function useInvalidateOrganizations() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ORGS_KEY });
}

export function useCreateOrganization() {
  const invalidate = useInvalidateOrganizations();
  return useMutation({
    mutationFn: async (input: { name: string } & OrgProfileFields): Promise<Organization> => {
      const { data, error } = await api.POST('/api/v1/admin/orgs', { body: input });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useUpdateOrganization() {
  const invalidate = useInvalidateOrganizations();
  return useMutation({
    mutationFn: async (
      input: { orgId: string; name?: string } & OrgProfileFields,
    ): Promise<Organization> => {
      const { orgId, ...body } = input;
      const { data, error } = await api.PATCH('/api/v1/admin/orgs/{org_id}', {
        params: { path: { org_id: orgId } },
        body,
      });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteOrganization() {
  const invalidate = useInvalidateOrganizations();
  return useMutation({
    mutationFn: async (orgId: string): Promise<void> => {
      const { error } = await api.DELETE('/api/v1/admin/orgs/{org_id}', {
        params: { path: { org_id: orgId } },
      });
      // 403 "can't delete your own org" / 409 "org still has workspaces or
      // users" surface here verbatim via problemDetail -- callers toast
      // err.message.
      if (error) throw new Error(problemDetail(error));
    },
    onSuccess: invalidate,
  });
}
