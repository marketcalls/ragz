import { type ReactNode } from 'react';

export function TopBar({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-line bg-bg px-4">
      <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h1>
      {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
    </header>
  );
}
