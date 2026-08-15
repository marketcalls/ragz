import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { components } from '@/api/schema';

// GET/PUT /api/v1/admin/sso + GET /api/v1/admin/orgs +
// PUT /api/v1/admin/orgs/{org_id}/sso-domains. Superadmin-only. Iron rule 3:
// the OIDC client_secret is write-only -- the GET response only ever carries
// the existence boolean (client_secret_set), never a value, and this module
// never invents a way to display one. It is sent in the PUT body only when the
// user actually typed a new value.
export type SsoConfig = components['schemas']['SsoConfigOut'];
export type AdminOrg = components['schemas']['OrgOut'];

export interface SsoConfigUpdate {
  issuer: string;
  client_id: string;
  client_secret?: string;
}

// Mirrors the `problemDetail` helper in features/admin/email/queries.ts -- kept
// local (not imported) so this feature stays self-contained within its own
// directory, per this task's file-ownership scope.
function problemDetail(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
    return body.detail || fallback;
  }
  return fallback;
}

const SSO_KEY = ['admin', 'sso'];
const ORGS_KEY = ['admin', 'orgs'];

export function useSsoConfig() {
  return useQuery({
    queryKey: SSO_KEY,
    queryFn: async (): Promise<SsoConfig> => {
      const { data, error } = await api.GET('/api/v1/admin/sso');
      if (error) throw new Error('failed to load SSO settings');
      return data;
    },
  });
}

export function usePutSsoConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (patch: SsoConfigUpdate): Promise<SsoConfig> => {
      const { data, error } = await api.PUT('/api/v1/admin/sso', { body: patch });
      if (error) throw new Error(problemDetail(error, 'failed to save SSO settings'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: SSO_KEY }),
  });
}

export function useAdminOrgs() {
  return useQuery({
    queryKey: ORGS_KEY,
    queryFn: async (): Promise<AdminOrg[]> => {
      const { data, error } = await api.GET('/api/v1/admin/orgs');
      if (error) throw new Error('failed to load organizations');
      return data;
    },
  });
}

export function usePutOrgSsoDomains() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { orgId: string; domains: string[] }): Promise<AdminOrg> => {
      const { data, error } = await api.PUT('/api/v1/admin/orgs/{org_id}/sso-domains', {
        params: { path: { org_id: input.orgId } },
        body: { domains: input.domains },
      });
      if (error) throw new Error(problemDetail(error, 'failed to save allowed domains'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ORGS_KEY }),
  });
}
