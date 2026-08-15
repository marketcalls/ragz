import { useState } from 'react';
import { TriangleAlert } from 'lucide-react';

import { ChartCard } from '@/components/charts/chart-card';
import { RadialGauge } from '@/components/charts/radial-gauge';
import { StackedArea } from '@/components/charts/stacked-area';
import { TopBar } from '@/components/layout/top-bar';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { StatTile } from '@/components/ui/stat-tile';

import { useUsageDaily, useUsageMe } from './queries';

const RANGES = [7, 30, 90] as const;

function formatResetDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
}

export function UsagePage() {
  const query = useUsageMe();
  const [days, setDays] = useState<number>(30);
  const dailyQuery = useUsageDaily(days);
  const data = query.data;
  // allocated_tokens is null when no allocation is configured (unlimited/
  // local deployments -- see UsageMeterOut) and RadialGauge itself refuses
  // to render for a non-positive max, so both null and 0 collapse to the
  // same "Unlimited" state here.
  const unlimited = data != null && !data.allocated_tokens;

  return (
    <>
      <TopBar
        title="My Usage"
        actions={
          <div className="flex gap-1">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setDays(r)}
                className={`rounded-md px-2 py-1 text-xs transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink ${
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
        {query.isError ? (
          <QueryError error={query.error} onRetry={() => query.refetch()} />
        ) : null}
        {data ? (
          <div className="mx-auto max-w-3xl space-y-6">
            {data.warning ? (
              <div className="flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-warning">
                <TriangleAlert className="h-4 w-4 shrink-0" aria-hidden />
                <p className="text-[13px] font-medium">
                  You&apos;re approaching your token limit.
                </p>
              </div>
            ) : null}

            <ChartCard title="Tokens used this period">
              {unlimited ? (
                <div className="flex h-full flex-col items-center justify-center gap-1">
                  <span className="text-2xl font-semibold tabular-nums text-ink">
                    {data.used_tokens.toLocaleString()}
                  </span>
                  <span className="text-xs text-secondary">Unlimited allocation</span>
                </div>
              ) : (
                <RadialGauge
                  value={data.used_tokens}
                  max={data.allocated_tokens as number}
                  label="Tokens used"
                  valueLabel={`${data.used_tokens.toLocaleString()} / ${(data.allocated_tokens as number).toLocaleString()}`}
                />
              )}
            </ChartCard>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <StatTile label="Used" value={data.used_tokens.toLocaleString()} />
              <StatTile
                label="Allocated"
                value={unlimited ? 'Unlimited' : (data.allocated_tokens as number).toLocaleString()}
              />
              <StatTile label="Resets" value={formatResetDate(data.resets_at)} />
            </div>

            <ChartCard title="Usage over time">
              {dailyQuery.isPending ? (
                <Spinner label="Loading usage…" />
              ) : dailyQuery.isError ? (
                <QueryError error={dailyQuery.error} onRetry={() => dailyQuery.refetch()} />
              ) : (
                <StackedArea
                  data={dailyQuery.data ?? []}
                  xKey="date"
                  keys={['prompt_tokens', 'completion_tokens']}
                />
              )}
            </ChartCard>
          </div>
        ) : null}
      </div>
    </>
  );
}
