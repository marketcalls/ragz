import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { ChartCard } from '@/components/charts/chart-card';
import { DonutChart } from '@/components/charts/donut-chart';
import { GroupedBar } from '@/components/charts/grouped-bar';
import { RadarChart } from '@/components/charts/radar-chart';
import { RadialGauge } from '@/components/charts/radial-gauge';
import { StackedArea } from '@/components/charts/stacked-area';
import { TimeSeriesLine } from '@/components/charts/time-series-line';
import { TopBar } from '@/components/layout/top-bar';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { StatTile } from '@/components/ui/stat-tile';
import { useAuthorization } from '@/lib/use-authorization';

import { useOrgUsage, useUsageSummary } from './queries';

const RANGES = [7, 30, 90] as const;

// The eval-trend radar shares one radial axis across its three series (the
// dataviz "one axis" rule), but hit_rate/citation_precision are already 0..1
// fractions while avg_faithfulness is a 1-5 LLM-judge score (see
// evals/models.py's `avg_faithfulness: ... # 1-5 scale`) -- so it's
// normalized to 0..1 (÷5) before joining the others, and the series label
// says so.
const FAITHFULNESS_SCALE = 5;
// Past this many workspaces the radar turns into unreadable overlapping
// polygons -- cap it rather than let it grow unbounded.
const MAX_RADAR_WORKSPACES = 8;

/** Pivot rows to one object per day; keep top-3 models by total, bucket rest as "Other".
 * Capped at top-3 (not top-4) so that with an "Other" bucket added there are at most 4
 * series total, matching categorical()'s 4-color cap - otherwise "Other" wraps around
 * to the accent color and collides with the top model's series. */
function pivotTokens(rows: { day: string; model_name: string; tokens: number }[]) {
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(r.model_name, (totals.get(r.model_name) ?? 0) + r.tokens);
  const top = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([m]) => m);
  const byDay = new Map<string, Record<string, string | number>>();
  for (const r of rows) {
    const key = top.includes(r.model_name) ? r.model_name : 'Other';
    const row = byDay.get(r.day) ?? { day: r.day };
    row[key] = ((row[key] as number | undefined) ?? 0) + r.tokens;
    byDay.set(r.day, row);
  }
  const keys = totals.size > 3 ? [...top, 'Other'] : top;
  return { data: [...byDay.values()], keys };
}

/** answer_quality scores are documented as 0..1 (chat/service.py's low-score
 * cutoff is a hardcoded `< 0.5`), but this defends against a future 0..100
 * scale change anyway: any value already above 1 is treated as a percent,
 * i.e. its own max. */
function scoreMax(value: number | null): number {
  return value != null && value > 1 ? 100 : 1;
}

function scorePercentLabel(value: number | null): string | undefined {
  if (value == null) return undefined;
  return `${Math.round((value / scoreMax(value)) * 100)}%`;
}

export function DashboardPage() {
  const [days, setDays] = useState<number>(30);
  const query = useUsageSummary(days);
  const authz = useAuthorization();
  const isSuperadmin = authz.data?.role === 'superadmin';
  const orgUsage = useOrgUsage(isSuperadmin);

  const pivoted = useMemo(
    () => pivotTokens(query.data?.tokens_by_model_per_day ?? []),
    [query.data],
  );

  const modelShare = useMemo(
    () => (query.data?.by_model ?? []).map((m) => ({ name: m.model_id ?? 'Unknown', value: m.tokens })),
    [query.data],
  );

  const evalRadarData = useMemo(
    () =>
      (query.data?.eval_trend ?? []).slice(0, MAX_RADAR_WORKSPACES).map((t) => ({
        workspace_name: t.workspace_name,
        'Hit rate': t.hit_rate ?? 0,
        'Citation precision': t.citation_precision ?? 0,
        'Faithfulness (of 5)':
          t.avg_faithfulness != null ? t.avg_faithfulness / FAITHFULNESS_SCALE : 0,
      })),
    [query.data],
  );

  const feedbackData = useMemo(() => {
    const f = query.data?.feedback_summary;
    if (!f || f.total_count === 0) return [];
    return [
      { name: 'Down', value: f.down_count },
      { name: 'Up', value: f.total_count - f.down_count },
    ];
  }, [query.data]);

  const orgBarData = useMemo(
    () => (orgUsage.data ?? []).map((o) => ({ name: o.name, tokens: o.tokens })),
    [orgUsage.data],
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
        {query.isError ? <QueryError error={query.error} onRetry={() => query.refetch()} /> : null}
        {query.data ? (
          <div className="mx-auto max-w-5xl space-y-6">
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatTile
                label="Queries"
                value={query.data.kpis.queries.toLocaleString()}
                sparkline={query.data.queries_per_day.map((d) => d.count)}
              />
              <StatTile
                label="Tokens"
                value={query.data.kpis.total_tokens.toLocaleString()}
                sparkline={query.data.by_day.map((d) => d.tokens)}
              />
              <StatTile label="Active users" value={String(query.data.kpis.active_users)} />
              <StatTile
                label="No-answer"
                value={String(query.data.kpis.no_answer_count)}
                sub="content gaps (ADM-4)"
              />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <ChartCard title="Queries per day">
                <TimeSeriesLine data={query.data.queries_per_day} />
              </ChartCard>
              <ChartCard title="Tokens by model, per day (top 3 + Other)">
                <StackedArea data={pivoted.data} xKey="day" keys={pivoted.keys} />
              </ChartCard>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <ChartCard title="Tokens by model, share of total">
                <DonutChart data={modelShare} centerLabel="tokens" />
              </ChartCard>
              <ChartCard title="Feedback — down vs. up, this window">
                <DonutChart data={feedbackData} centerLabel="responses" />
              </ChartCard>
            </div>

            <ChartCard title="Answer quality — avg grounding & completeness (0–1 scale)">
              <div className="flex h-full items-center justify-around gap-4">
                <div className="h-40 w-40">
                  <RadialGauge
                    value={query.data.answer_quality.avg_grounding_score ?? Number.NaN}
                    max={scoreMax(query.data.answer_quality.avg_grounding_score)}
                    label="Grounding"
                    valueLabel={scorePercentLabel(query.data.answer_quality.avg_grounding_score)}
                  />
                </div>
                <div className="h-40 w-40">
                  <RadialGauge
                    value={query.data.answer_quality.avg_completeness_score ?? Number.NaN}
                    max={scoreMax(query.data.answer_quality.avg_completeness_score)}
                    label="Completeness"
                    valueLabel={scorePercentLabel(query.data.answer_quality.avg_completeness_score)}
                  />
                </div>
                <div className="text-center">
                  <p className="text-2xl font-semibold tabular-nums text-ink">
                    {query.data.answer_quality.low_score_count}
                  </p>
                  <p className="mt-0.5 text-xs text-secondary">low-scoring</p>
                  <p className="mt-2 text-xs text-secondary">
                    {`of ${query.data.answer_quality.audited_count} audited`}
                  </p>
                </div>
              </div>
            </ChartCard>

            <ChartCard title="Eval trend — latest run per workspace">
              <RadarChart
                data={evalRadarData}
                categoryKey="workspace_name"
                keys={['Hit rate', 'Citation precision', 'Faithfulness (of 5)']}
              />
            </ChartCard>

            {isSuperadmin && orgBarData.length > 0 ? (
              <ChartCard title="Cross-org token usage (platform-wide, superadmin)">
                <GroupedBar data={orgBarData} categoryKey="name" keys={['tokens']} />
              </ChartCard>
            ) : null}

            {/* Top users stays a table, not a chart: queries and tokens differ by
                orders of magnitude, so a shared-axis bar would misrepresent one of
                them, and the email column has nothing chartable to plot it against. */}
            <div className="rounded-lg border border-line shadow-soft">
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
            {query.data.worst_answers.length > 0 ? (
              <div className="rounded-lg border border-line shadow-soft">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-raised text-left text-xs text-secondary">
                      <th className="px-4 py-2 font-medium">Lowest-scoring answers</th>
                      <th className="px-4 py-2 text-right font-medium">Grounding</th>
                      <th className="px-4 py-2 text-right font-medium">Completeness</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.worst_answers.map((w) => (
                      <tr key={w.message_id} className="border-t border-line-faint">
                        <td className="px-4 py-2 text-ink">
                          <Link to={`/chat/${w.chat_id}`} className="hover:underline">
                            {w.content_snippet.slice(0, 80)}
                            {w.content_snippet.length > 80 ? '…' : ''}
                          </Link>
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          {w.grounding_score != null ? w.grounding_score.toFixed(2) : '—'}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          {w.completeness_score != null ? w.completeness_score.toFixed(2) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  );
}
