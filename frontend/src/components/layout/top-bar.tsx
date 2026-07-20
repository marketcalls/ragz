import { type ReactNode } from 'react';

export function TopBar({
  title,
  caption,
  actions,
}: {
  title: string;
  // Task 9 (spec §5): minimal "memory summary visible in a long chat"
  // indicator -- a small subdued label next to the title, no interaction.
  caption?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-line bg-bg px-4">
      <div className="flex items-center gap-2">
        <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h1>
        {caption ? <span className="text-xs text-secondary">{caption}</span> : null}
      </div>
      {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
    </header>
  );
}
