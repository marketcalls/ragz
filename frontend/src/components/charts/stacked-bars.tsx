import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { categorical, useChartPalette } from '@/lib/chart-palette';

export function StackedBars({
  data, keys,
}: { data: Record<string, string | number>[]; keys: string[] }) {
  const p = useChartPalette();
  const colors = categorical(p, keys.length);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke={p.grid} vertical={false} />
        <XAxis dataKey="day" tick={{ fill: p.axis, fontSize: 12 }} tickLine={false}
               axisLine={{ stroke: p.grid }} minTickGap={24} />
        <YAxis tick={{ fill: p.axis, fontSize: 12 }} tickLine={false} axisLine={false} />
        <Tooltip cursor={{ fill: 'transparent' }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} stackId="a"
               fill={colors[i % colors.length]} isAnimationActive={false} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
