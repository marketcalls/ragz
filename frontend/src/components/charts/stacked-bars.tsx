import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { CHART_ANIMATION } from '@/lib/chart-animation';
import { categorical, useChartPalette } from '@/lib/chart-palette';

export function StackedBars({
  data, xKey, keys,
}: { data: Record<string, string | number>[]; xKey: string; keys: string[] }) {
  const p = useChartPalette();

  if (data.length === 0 || keys.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  const colors = categorical(p, keys.length);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke={p.grid} vertical={false} />
        <XAxis dataKey={xKey} tick={{ fill: p.axis, fontSize: 12 }} tickLine={false}
               axisLine={{ stroke: p.grid }} minTickGap={24} />
        <YAxis tick={{ fill: p.axis, fontSize: 12 }} tickLine={false} axisLine={false} />
        <Tooltip cursor={{ fill: 'transparent' }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} stackId="a" fill={colors[i % colors.length]} {...CHART_ANIMATION} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
