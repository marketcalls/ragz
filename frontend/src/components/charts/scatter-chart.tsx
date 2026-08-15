import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart as RechartsScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';

import { CHART_ANIMATION } from '@/lib/chart-animation';
import { useChartPalette } from '@/lib/chart-palette';

const tooltipContentStyle = {
  backgroundColor: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--r-md)',
  fontSize: 12,
  color: 'var(--text)',
};

// Two numeric axes (xKey/yKey), optionally a third dimension encoded as
// bubble size (zKey) -- e.g. plotting query latency against token count,
// bubble size for request volume. Single series (no categorical grouping
// key in the ChartBlock shape), so every point shares one accent fill.
export function ScatterChart({
  data, xKey, yKey, zKey,
}: { data: Record<string, number | string>[]; xKey: string; yKey: string; zKey?: string }) {
  const p = useChartPalette();

  if (data.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <RechartsScatterChart margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke={p.grid} />
        <XAxis
          dataKey={xKey}
          type="number"
          name={xKey}
          tick={{ fill: p.axis, fontSize: 12 }}
          tickLine={false}
          axisLine={{ stroke: p.grid }}
        />
        <YAxis
          dataKey={yKey}
          type="number"
          name={yKey}
          tick={{ fill: p.axis, fontSize: 12 }}
          tickLine={false}
          axisLine={false}
        />
        {zKey ? <ZAxis dataKey={zKey} name={zKey} range={[64, 400]} /> : null}
        <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={tooltipContentStyle} />
        <Scatter data={data} fill={p.accent} {...CHART_ANIMATION} />
      </RechartsScatterChart>
    </ResponsiveContainer>
  );
}
