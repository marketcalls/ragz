import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';

// `title` is optional (block-renderer.tsx's ChartBlock has no required title)
// -- every existing caller still passes one, so this is additive.
export function ChartCard({
  title, subtitle, children,
}: { title?: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      {title ? <h3 className="text-sm font-semibold text-ink">{title}</h3> : null}
      {subtitle ? <p className="mt-0.5 text-[12px] text-secondary">{subtitle}</p> : null}
      <div className={cn('h-64', (title || subtitle) && 'mt-3')}>{children}</div>
    </div>
  );
}
