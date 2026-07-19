import { useMemo, useState } from 'react';

import { ChartCard } from '@/components/charts/chart-card';
import { StackedBars } from '@/components/charts/stacked-bars';
import { TimeSeriesLine } from '@/components/charts/time-series-line';
import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';
import { StatTile } from '@/components/ui/stat-tile';

import { useUsageSummary } from './queries';

const RANGES = [7, 30, 90] as const;

/** Pivot rows to one object per day; keep top-4 models by total, bucket rest as "Other". */
function pivotTokens(rows: { day: string; model_name: string; tokens: number }[]) {
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(r.model_name, (totals.get(r.model_name) ?? 0) + r.tokens);
  const top = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([m]) => m);
  const byDay = new Map<string, Record<string, string | number>>();
  for (const r of rows) {
    const key = top.includes(r.model_name) ? r.model_name : 'Other';
    const row = byDay.get(r.day) ?? { day: r.day };
    row[key] = ((row[key] as number | undefined) ?? 0) + r.tokens;
    byDay.set(r.day, row);
  }
  const keys = totals.size > 4 ? [...top, 'Other'] : top;
  return { data: [...byDay.values()], keys };
}

export function DashboardPage() {
  const [days, setDays] = useState<number>(30);
  const query = useUsageSummary(days);
  const pivoted = useMemo(
    () => pivotTokens(query.data?.tokens_by_model_per_day ?? []),
    [query.data],
  );

  return (
    <>
      <TopBar
        title="Usage dashboard"
        actions={
          <div className="flex gap-1">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setDays(r)}
                className={`rounded-md px-2 py-1 text-xs ${
                  days === r ? 'bg-subtle text-ink' : 'text-secondary'
                }`}
              >
                {r}d
              </button>
            ))}
          </div>
        }
      />
      <div className="flex-1 overflow-y-auto p-6">
        {query.isPending ? <Spinner label="Loading usage…" /> : null}
        {query.data ? (
          <div className="mx-auto max-w-5xl space-y-6">
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatTile label="Queries" value={query.data.kpis.queries.toLocaleString()} />
              <StatTile label="Tokens" value={query.data.kpis.total_tokens.toLocaleString()} />
              <StatTile label="Active users" value={String(query.data.kpis.active_users)} />
              <StatTile
                label="No-answer"
                value={String(query.data.kpis.no_answer_count)}
                sub="content gaps (ADM-4)"
              />
            </div>
            <ChartCard title="Queries per day">
              <TimeSeriesLine data={query.data.queries_per_day} />
            </ChartCard>
            <ChartCard title="Tokens by model per day">
              <StackedBars data={pivoted.data} keys={pivoted.keys} />
            </ChartCard>
            <div className="rounded-lg border border-line">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-raised text-left text-xs text-secondary">
                    <th className="px-4 py-2 font-medium">Top users</th>
                    <th className="px-4 py-2 text-right font-medium">Queries</th>
                    <th className="px-4 py-2 text-right font-medium">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.by_user.map((u) => (
                    <tr key={u.user_id} className="border-t border-line-faint">
                      <td className="px-4 py-2 text-ink">{u.email}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{u.queries}</td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {u.tokens.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>
    </>
  );
}
