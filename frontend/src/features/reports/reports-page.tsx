import { Download } from 'lucide-react';
import { useMemo, useState } from 'react';

import { ChartCard } from '@/components/charts/chart-card';
import { GroupedBar } from '@/components/charts/grouped-bar';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { QueryError } from '@/components/ui/query-error';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { type Authorization, useAuthorization } from '@/lib/use-authorization';

import {
  exportUsageReport,
  useUsageReport,
  type ReportGroupBy,
  type ReportParams,
  type ReportScope,
} from './queries';

// Lifetime maps to the endpoint's ge=1,le=36500 cap (~100y) -- the largest
// window reports.py accepts, i.e. "all history".
const RANGES = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
  { label: 'Lifetime', days: 36500 },
] as const;

const SCOPE_LABELS: Record<ReportScope, string> = {
  self: 'My usage',
  department: 'Department',
  org: 'Organization',
  platform: 'Platform (all orgs)',
};

const GROUP_BY_OPTIONS: { value: ReportGroupBy; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: 'user', label: 'User' },
  { value: 'workspace', label: 'Department (workspace)' },
  { value: 'feature', label: 'Feature' },
  { value: 'model', label: 'Model' },
];

// Mirrors reports.py's _require_scope EXACTLY: self is the floor everyone
// reaching this page holds; department/org are the matching actions (admins
// auto-hold them); platform is gated on the superadmin ROLE only -- never the
// reports.view.platform action, which an org admin also auto-holds and which
// would otherwise leak cross-org data. Returned broadest-last so the last
// entry is the sensible default scope.
function allowedScopes(auth: Authorization | undefined): ReportScope[] {
  const scopes: ReportScope[] = ['self'];
  const isSuper = auth?.role === 'superadmin';
  const has = (action: string) => auth?.permissions.has(action) === true;
  if (isSuper || has('reports.view.department')) scopes.push('department');
  if (isSuper || has('reports.view.org')) scopes.push('org');
  if (isSuper) scopes.push('platform');
  return scopes;
}

function usd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function ReportsPage() {
  const { data: auth } = useAuthorization();
  const scopes = useMemo(() => allowedScopes(auth), [auth]);
  // Default to the broadest scope the caller can use (superadmin -> platform,
  // admin -> org, else self). `scopes` is never empty (self is always first).
  const [scope, setScope] = useState<ReportScope | null>(null);
  const [days, setDays] = useState<number>(30);
  const [groupBy, setGroupBy] = useState<ReportGroupBy>('day');

  // `scopes` always contains at least 'self', so the fallback is unreachable --
  // it only satisfies noUncheckedIndexedAccess on the array index.
  const effectiveScope: ReportScope = scope ?? scopes[scopes.length - 1] ?? 'self';
  const params: ReportParams = { scope: effectiveScope, days, group_by: groupBy };
  const query = useUsageReport(params);
  // Memoized so the `?? []` fallback isn't a fresh array each render, which
  // would thrash the chartData/totals useMemo deps below.
  const rows = useMemo(() => query.data?.rows ?? [], [query.data]);

  const chartData = useMemo(
    () =>
      rows.map((r) => ({
        group: r.group,
        'Prompt tokens': r.prompt_tokens,
        'Completion tokens': r.completion_tokens,
        'Est. cost (USD)': r.cost_usd,
      })),
    [rows],
  );

  const totals = useMemo(
    () =>
      rows.reduce(
        (acc, r) => ({
          prompt_tokens: acc.prompt_tokens + r.prompt_tokens,
          completion_tokens: acc.completion_tokens + r.completion_tokens,
          units: acc.units + r.units,
          cost_usd: acc.cost_usd + r.cost_usd,
        }),
        { prompt_tokens: 0, completion_tokens: 0, units: 0, cost_usd: 0 },
      ),
    [rows],
  );

  return (
    <>
      <TopBar
        title="Reports"
        actions={
          <div className="flex items-center gap-3">
            <div className="flex gap-1">
              {RANGES.map((r) => (
                <button
                  key={r.days}
                  type="button"
                  onClick={() => setDays(r.days)}
                  className={`rounded-md px-2 py-1 text-xs transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink ${
                    days === r.days ? 'bg-subtle text-ink' : 'text-secondary'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <Button size="sm" onClick={() => void exportUsageReport(params)}>
              <Download className="h-3.5 w-3.5" aria-hidden /> Export CSV
            </Button>
          </div>
        }
      />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-5xl space-y-6">
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1">
              <span className="text-[12px] font-medium text-secondary">Scope</span>
              <NativeSelect
                aria-label="Scope"
                className="w-52"
                value={effectiveScope}
                onChange={(e) => setScope(e.target.value as ReportScope)}
              >
                {scopes.map((s) => (
                  <option key={s} value={s}>
                    {SCOPE_LABELS[s]}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[12px] font-medium text-secondary">Group by</span>
              <NativeSelect
                aria-label="Group by"
                className="w-52"
                value={groupBy}
                onChange={(e) => setGroupBy(e.target.value as ReportGroupBy)}
              >
                {GROUP_BY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </NativeSelect>
            </label>
          </div>

          <p className="text-[12px] text-secondary">
            Estimated cost is derived from the configured model and API rates (LLM tokens,
            reranker and web-search per-call rates); treat the USD figures as an approximation,
            not a billed amount.
          </p>

          {query.isPending ? <Spinner label="Loading report…" /> : null}
          {query.isError ? (
            <QueryError error={query.error} onRetry={() => query.refetch()} />
          ) : null}

          {query.data ? (
            rows.length === 0 ? (
              <div className="rounded-lg border border-line p-10 text-center text-sm text-secondary">
                No usage in this range.
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <ChartCard title="Tokens by group">
                    <GroupedBar
                      data={chartData}
                      categoryKey="group"
                      keys={['Prompt tokens', 'Completion tokens']}
                    />
                  </ChartCard>
                  <ChartCard title="Estimated cost by group (USD)">
                    <GroupedBar data={chartData} categoryKey="group" keys={['Est. cost (USD)']} />
                  </ChartCard>
                </div>

                <Table>
                  <THead>
                    <TR>
                      <TH>Group</TH>
                      <TH className="text-right">Prompt tokens</TH>
                      <TH className="text-right">Completion tokens</TH>
                      <TH className="text-right">Units (calls)</TH>
                      <TH className="text-right">Est. cost (USD)</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {rows.map((r) => (
                      <TR key={r.group}>
                        <TD className="font-medium">{r.group}</TD>
                        <TD className="text-right">{r.prompt_tokens.toLocaleString()}</TD>
                        <TD className="text-right">{r.completion_tokens.toLocaleString()}</TD>
                        <TD className="text-right">{r.units.toLocaleString()}</TD>
                        <TD className="text-right">{usd(r.cost_usd)}</TD>
                      </TR>
                    ))}
                    <TR className="border-t-2 border-line font-semibold">
                      <TD>Total</TD>
                      <TD className="text-right">{totals.prompt_tokens.toLocaleString()}</TD>
                      <TD className="text-right">{totals.completion_tokens.toLocaleString()}</TD>
                      <TD className="text-right">{totals.units.toLocaleString()}</TD>
                      <TD className="text-right">{usd(totals.cost_usd)}</TD>
                    </TR>
                  </TBody>
                </Table>
              </>
            )
          ) : null}
        </div>
      </div>
    </>
  );
}
