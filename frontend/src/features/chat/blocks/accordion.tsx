import { ChevronDown } from 'lucide-react';
import { useState } from 'react';

import type { AccordionBlock as AccordionBlockT } from '@/api/types';
import { cn } from '@/lib/cn';

import { BlockRenderer } from './block-renderer';
import type { SourceChipData } from '../source-panel';

// Task 6 (generative-UI design 2026-08-16, §6): a foldable accordion,
// visual family shared with TabsView (chevron pattern mirrors
// behind-the-scenes.tsx's StepCard/BehindTheScenes toggles). Sections open
// independently -- local Set<number> of open indices, not a single active
// index. Backend types nest out statically (AccordionItem.blocks is
// InnerBlock, which excludes TabsBlock/AccordionBlock), but this component
// guards defensively too rather than trust the server-validated shape
// blindly -- same posture as TabsView's `depth > 0` guard.
export function Accordion({
  block,
  depth,
  onFormSubmit,
  onFollowUp,
  onOpenDocument,
}: {
  block: AccordionBlockT;
  depth: number;
  onFormSubmit?: (message: string) => void;
  onFollowUp?: (message: string) => void;
  onOpenDocument?: (source: SourceChipData) => void;
}) {
  const [openIndices, setOpenIndices] = useState<Set<number>>(() => new Set());
  if (depth > 0 || block.items.length === 0) return null;

  const toggle = (i: number): void => {
    setOpenIndices((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line bg-bg p-3">
      {block.items.map((item, i) => {
        const open = openIndices.has(i);
        return (
          <div key={i} className="rounded-md border border-line bg-raised">
            <button
              type="button"
              onClick={() => toggle(i)}
              aria-expanded={open}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[13px] font-medium text-ink hover:text-accent"
            >
              <ChevronDown
                className={cn(
                  'h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-150 ease-out',
                  open && 'rotate-180',
                )}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
            </button>
            {open ? (
              <div className="border-t border-line px-2.5 pb-2.5">
                <BlockRenderer
                  blocks={item.blocks}
                  depth={depth + 1}
                  onFormSubmit={onFormSubmit}
                  onFollowUp={onFollowUp}
                  onOpenDocument={onOpenDocument}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
