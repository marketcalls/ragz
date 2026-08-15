import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { CHART_ANIMATION } from '@/lib/chart-animation';
import { useChartPalette } from '@/lib/chart-palette';

export function TimeSeriesLine({ data }: { data: { day: string; count: number }[] }) {
  const p = useChartPalette();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke={p.grid} vertical={false} />
        <XAxis dataKey="day" tick={{ fill: p.axis, fontSize: 12 }} tickLine={false}
               axisLine={{ stroke: p.grid }} minTickGap={24} />
        <YAxis tick={{ fill: p.axis, fontSize: 12 }} tickLine={false} axisLine={false}
               allowDecimals={false} />
        <Tooltip cursor={{ stroke: p.grid }} />
        <Line type="monotone" dataKey="count" stroke={p.accent} strokeWidth={2}
              dot={false} {...CHART_ANIMATION} />
      </LineChart>
    </ResponsiveContainer>
  );
}
