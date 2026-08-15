import { Cell, PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer } from 'recharts';

import { useChartPalette } from '@/lib/chart-palette';

// Thresholds are fractions of `max` at which the fill shifts from accent to
// warning to danger — e.g. quota usage creeping toward its allocation.
const WARNING_AT = 0.7;
const DANGER_AT = 0.9;

export function RadialGauge({
  value, max, label, valueLabel,
}: { value: number; max: number; label?: string; valueLabel?: string }) {
  const p = useChartPalette();

  if (!Number.isFinite(value) || !Number.isFinite(max) || max <= 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  const ratio = Math.min(Math.max(value / max, 0), 1);
  const color = ratio >= DANGER_AT ? p.danger : ratio >= WARNING_AT ? p.warning : p.accent;
  const data = [{ name: label ?? 'value', value: ratio * 100 }];

  return (
    <div className="relative h-full w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          data={data}
          startAngle={90}
          endAngle={-270}
          innerRadius="72%"
          outerRadius="100%"
          barSize={12}
        >
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} axisLine={false} />
          <RadialBar dataKey="value" background={{ fill: p.grid }} cornerRadius={6} isAnimationActive={false}>
            <Cell fill={color} />
          </RadialBar>
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-semibold tabular-nums text-ink">
          {valueLabel ?? value.toLocaleString()}
        </span>
        {label ? <span className="mt-0.5 text-xs text-secondary">{label}</span> : null}
      </div>
    </div>
  );
}
