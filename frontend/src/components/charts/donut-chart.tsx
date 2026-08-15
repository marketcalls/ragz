import { Cell, Legend, type LegendPayload, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import { categorical, useChartPalette } from '@/lib/chart-palette';

// Recharts renders tooltip content as a plain DOM node (not an SVG
// presentation attribute), so var() resolves fine here — unlike Cell/Bar
// `fill`, which needs a resolved hex from the palette hook.
const tooltipContentStyle = {
  backgroundColor: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--r-md)',
  fontSize: 12,
  color: 'var(--text)',
};

export function DonutChart({
  data, centerLabel,
}: { data: { name: string; value: number }[]; centerLabel?: string }) {
  const p = useChartPalette();

  if (data.length === 0) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">No data</div>;
  }

  const colors = categorical(p, data.length);
  const total = data.reduce((sum, d) => sum + d.value, 0);

  return (
    <div className="relative h-full w-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="60%"
            outerRadius="100%"
            paddingAngle={2}
            isAnimationActive={false}
          >
            {data.map((d, i) => (
              <Cell key={d.name} fill={colors[i % colors.length]} />
            ))}
          </Pie>
          <Tooltip contentStyle={tooltipContentStyle} />
          <Legend
            wrapperStyle={{ fontSize: 12 }}
            // Default legend content shows only the series name -- augment it
            // (rather than replace it) so each row also shows its numeric
            // value, right-aligned tabular-nums, mirroring openui's side
            // legend. `entry`/index line up 1:1 with the `data` prop, which
            // recharts doesn't otherwise expose through the payload.
            formatter={(value: string, _entry: LegendPayload, index: number) => {
              const point = data[index];
              return (
                <span className="inline-flex items-center gap-1.5">
                  <span>{value}</span>
                  {point ? (
                    <span className="tabular-nums text-secondary">{point.value.toLocaleString()}</span>
                  ) : null}
                </span>
              );
            }}
          />
        </PieChart>
      </ResponsiveContainer>
      {centerLabel ? (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center pb-6">
          <span className="text-xl font-semibold tabular-nums text-ink">{total.toLocaleString()}</span>
          <span className="mt-0.5 text-xs text-secondary">{centerLabel}</span>
        </div>
      ) : null}
    </div>
  );
}
