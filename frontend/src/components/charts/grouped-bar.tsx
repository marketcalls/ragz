import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { categorical, useChartPalette } from '@/lib/chart-palette';

const tooltipContentStyle = {
  backgroundColor: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--r-md)',
  fontSize: 12,
  color: 'var(--text)',
};

// Grouped (side-by-side) bars — no stackId, unlike StackedBars.
export function GroupedBar({
  data, categoryKey, keys,
}: { data: Record<string, string | number>[]; categoryKey: string; keys: string[] }) {
  const p = useChartPalette();

  if (data.length === 0 || keys.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  const colors = categorical(p, keys.length);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke={p.grid} vertical={false} />
        <XAxis dataKey={categoryKey} tick={{ fill: p.axis, fontSize: 12 }} tickLine={false}
               axisLine={{ stroke: p.grid }} minTickGap={24} />
        <YAxis tick={{ fill: p.axis, fontSize: 12 }} tickLine={false} axisLine={false} />
        <Tooltip cursor={{ fill: 'transparent' }} contentStyle={tooltipContentStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} fill={colors[i % colors.length]} isAnimationActive={false} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
