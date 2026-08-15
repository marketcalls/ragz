import type { StepsBlock as StepsBlockT } from '@/api/types';
import { cn } from '@/lib/cn';

// T-C (2026-08-16): a vertical numbered stepper. Pure display -- no
// interactivity, no threaded handlers -- same visual family as InfoCard/
// Accordion (numbered-circle chip mirrors RankedList's rank badge).
export function Steps({ block }: { block: StepsBlockT }) {
  if (block.items.length === 0) return null;
  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      <ol className="flex flex-col">
        {block.items.map((item, i) => {
          const isLast = i === block.items.length - 1;
          return (
            <li key={i} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[12px] font-medium tabular-nums text-accent-on-soft">
                  {i + 1}
                </span>
                {!isLast ? <span className="w-px flex-1 bg-line" aria-hidden /> : null}
              </div>
              <div className={cn('min-w-0', !isLast && 'pb-4')}>
                <p className="text-[13px] font-medium text-ink">{item.title}</p>
                {item.details ? <p className="mt-0.5 text-[12px] text-secondary">{item.details}</p> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
