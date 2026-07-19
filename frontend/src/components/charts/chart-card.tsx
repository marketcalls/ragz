import type { ReactNode } from 'react';

export function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      <h3 className="mb-3 text-sm font-semibold text-ink">{title}</h3>
      <div className="h-64">{children}</div>
    </div>
  );
}
