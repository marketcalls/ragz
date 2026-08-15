import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

// Mirrors reports.py's Scope/GroupBy literals (design 2026-08-15). The scope
// the caller may actually pick is derived in reports-page.tsx from
// /me/authorization -- the backend re-enforces it (require_action floor +
// per-scope escalation), so a wrong scope here just 403s.
export type ReportScope = 'self' | 'department' | 'org' | 'platform';
export type ReportGroupBy = 'day' | 'user' | 'workspace' | 'feature' | 'model';

export interface ReportParams {
  scope: ReportScope;
  days: number;
  group_by: ReportGroupBy;
}

export function useUsageReport(params: ReportParams) {
  return useQuery({
    queryKey: ['reports-usage', params.scope, params.days, params.group_by],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/reports/usage', {
        params: { query: params },
      });
      if (error) throw new Error('failed to load usage report');
      return data;
    },
  });
}

// The export route streams text/csv with a Content-Disposition attachment
// header. openapi-fetch can't type a CSV body (the OpenAPI content is
// `unknown`), so we read it as a Blob via parseAs and trigger the download the
// same way audit/csv.ts's downloadCsv does -- object URL + synthetic click.
export async function exportUsageReport(params: ReportParams): Promise<void> {
  const { data, error } = await api.GET('/api/v1/reports/usage/export', {
    params: { query: params },
    parseAs: 'blob',
  });
  if (error) throw new Error('failed to export usage report');
  const blob = data as Blob;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `ragz-usage-${params.scope}-${params.days}d.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
