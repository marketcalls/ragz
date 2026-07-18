import { type ReactNode } from 'react';

import { cn } from '@/lib/cn';

const TONES = {
  success: 'bg-success-soft text-success',
  accent: 'bg-accent-soft text-accent-on-soft',
  danger: 'bg-danger-soft text-danger',
  warning: 'bg-warning-soft text-warning',
} as const;

export type StatusTone = keyof typeof TONES;

export function StatusPill({
  tone,
  className,
  children,
}: {
  tone: StatusTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[12px] font-medium',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
