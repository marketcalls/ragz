import { Area, AreaChart, Bar, BarChart, Line, LineChart, ResponsiveContainer } from 'recharts';

import { useChartPalette } from '@/lib/chart-palette';

const MARGIN = { top: 2, right: 0, bottom: 0, left: 0 };

/** Tiny, axis-less, legend-less chart for embedding inline in a KPI tile
 * (openui Mini{Line,Bar,Area}Chart idea). Fixed small height — the parent
 * controls width. */
export function Sparkline({
  data, variant = 'line',
}: { data: number[]; variant?: 'line' | 'bar' | 'area' }) {
  const p = useChartPalette();

  if (data.length === 0) {
    return <div className="flex h-8 items-center justify-center text-xs text-muted">No data</div>;
  }

  const points = data.map((value, i) => ({ i, value }));

  return (
    <div className="h-8 w-full">
      <ResponsiveContainer width="100%" height="100%">
        {variant === 'bar' ? (
          <BarChart data={points} margin={MARGIN}>
            <Bar dataKey="value" fill={p.accent} isAnimationActive={false} radius={[1, 1, 0, 0]} />
          </BarChart>
        ) : variant === 'area' ? (
          <AreaChart data={points} margin={MARGIN}>
            <Area
              type="monotone"
              dataKey="value"
              stroke={p.accent}
              fill={p.accent}
              fillOpacity={0.2}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        ) : (
          <LineChart data={points} margin={MARGIN}>
            <Line
              type="monotone"
              dataKey="value"
              stroke={p.accent}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
