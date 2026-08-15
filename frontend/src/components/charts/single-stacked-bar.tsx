import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { CHART_ANIMATION } from '@/lib/chart-animation';
import { categorical, useChartPalette } from '@/lib/chart-palette';

const tooltipContentStyle = {
  backgroundColor: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--r-md)',
  fontSize: 12,
  color: 'var(--text)',
};

// A single 100%-stacked bar (one row) split into proportional segments, one
// per key -- e.g. "Approved 40 / Pending 30 / Rejected 30" as one bar rather
// than a donut. `data` is a single row (already validated/coerced numeric by
// the caller); it's wrapped into a one-item array because recharts'
// BarChart always operates over an array of rows.
export function SingleStackedBar({
  data, keys,
}: { data: Record<string, number>; keys: string[] }) {
  const p = useChartPalette();

  if (keys.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  const colors = categorical(p, keys.length);
  const rows = [{ name: 'total', ...data }];

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" hide />
        <Tooltip cursor={{ fill: 'transparent' }} contentStyle={tooltipContentStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} stackId="a" fill={colors[i % colors.length]} {...CHART_ANIMATION} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
