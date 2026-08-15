import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

// GET/PUT /api/v1/admin/email + POST /api/v1/admin/email/test (Task 5,
// backend; Task 9, this page). Superadmin-only. Iron rule 3: the two
// provider secrets (smtp_password, ses_secret_key) are write-only -- the GET
// response only ever carries existence booleans (*_set), never a value, and
// this module never invents a way to display one.
export type EmailProvider = 'smtp' | 'ses';

export interface EmailConfig {
  provider: EmailProvider;
  from_email: string;
  from_name: string;
  smtp_host: string;
  smtp_port: number;
  smtp_use_tls: boolean;
  smtp_username: string;
  ses_region: string;
  ses_access_key_id: string;
  smtp_password_set: boolean;
  ses_secret_key_set: boolean;
}

export interface EmailConfigUpdate {
  provider: EmailProvider;
  from_email: string;
  from_name: string;
  smtp_host: string;
  smtp_port: number;
  smtp_use_tls: boolean;
  smtp_username: string;
  ses_region: string;
  ses_access_key_id: string;
  smtp_password?: string;
  ses_secret_key?: string;
}

// Mirrors the `problemDetail` helper in features/auth/mutations.ts -- kept
// local (not imported) so this feature stays self-contained within its own
// directory, per this task's file-ownership scope.
function problemDetail(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
    return body.detail || fallback;
  }
  return fallback;
}

const KEY = ['admin', 'email'];

export function useEmailConfig() {
  return useQuery({
    queryKey: KEY,
    queryFn: async (): Promise<EmailConfig> => {
      const { data, error } = await api.GET('/api/v1/admin/email');
      if (error) throw new Error('failed to load email settings');
      return data;
    },
  });
}

export function useUpdateEmailConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (patch: EmailConfigUpdate): Promise<EmailConfig> => {
      const { data, error } = await api.PUT('/api/v1/admin/email', { body: patch });
      if (error) throw new Error(problemDetail(error, 'failed to save email settings'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

export function useSendTestEmail() {
  return useMutation({
    mutationFn: async (to: string): Promise<void> => {
      // 502 application/problem+json on a misconfigured provider/send
      // failure (see schema.d.ts's send_test_email_route doc comment) --
      // surface its `detail` rather than a generic message.
      const { error } = await api.POST('/api/v1/admin/email/test', { body: { to } });
      if (error) throw new Error(problemDetail(error, 'failed to send test email'));
    },
  });
}
