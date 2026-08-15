import { Sparkline } from '@/components/charts/sparkline';

export function StatTile({
  label, value, sub, sparkline,
}: { label: string; value: string; sub?: string; sparkline?: number[] }) {
  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-secondary">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{value}</p>
      {sub ? <p className="mt-0.5 text-xs text-secondary">{sub}</p> : null}
      {sparkline ? (
        <div className="mt-2">
          <Sparkline data={sparkline} />
        </div>
      ) : null}
    </div>
  );
}
