import {
  Legend, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar,
  RadarChart as ReRadarChart, ResponsiveContainer, Tooltip,
} from 'recharts';

import { categorical, useChartPalette } from '@/lib/chart-palette';

const tooltipContentStyle = {
  backgroundColor: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--r-md)',
  fontSize: 12,
  color: 'var(--text)',
};

export function RadarChart({
  data, categoryKey, keys,
}: { data: Record<string, string | number>[]; categoryKey: string; keys: string[] }) {
  const p = useChartPalette();

  if (data.length === 0 || keys.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  const colors = categorical(p, keys.length);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ReRadarChart data={data}>
        <PolarGrid stroke={p.grid} />
        <PolarAngleAxis dataKey={categoryKey} tick={{ fill: p.axis, fontSize: 12 }} />
        <PolarRadiusAxis tick={{ fill: p.axis, fontSize: 11 }} axisLine={false} />
        <Tooltip contentStyle={tooltipContentStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => (
          <Radar
            key={k}
            dataKey={k}
            name={k}
            stroke={colors[i % colors.length]}
            fill={colors[i % colors.length]}
            fillOpacity={0.25}
            isAnimationActive={false}
          />
        ))}
      </ReRadarChart>
    </ResponsiveContainer>
  );
}
