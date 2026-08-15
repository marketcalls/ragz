import type { CarouselBlock as CarouselBlockT } from '@/api/types';

import { BlockRenderer } from './block-renderer';
import type { SourceChipData } from '../source-panel';

// T-C (2026-08-16): a horizontally scrollable, scroll-snap carousel. No
// external carousel dependency -- native overflow-x + scroll-snap only.
// Each slide recurses into the shared BlockRenderer for its own item.blocks,
// threading depth+1 same as TabsView/Accordion. Backend types nest out
// statically (CarouselItem.blocks excludes TabsBlock/AccordionBlock/
// CarouselBlock), but this component guards defensively too -- same posture
// as TabsView's/Accordion's `depth > 0` guard: carousel is top-level only.
export function Carousel({
  block,
  depth,
  onFormSubmit,
  onFollowUp,
  onOpenDocument,
}: {
  block: CarouselBlockT;
  depth: number;
  onFormSubmit?: (message: string) => void;
  onFollowUp?: (message: string) => void;
  onOpenDocument?: (source: SourceChipData) => void;
}) {
  if (depth > 0 || block.items.length === 0) return null;
  return (
    <div className="flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1">
      {block.items.map((item, i) => (
        <div
          key={i}
          className="w-[85%] shrink-0 snap-start rounded-lg border border-line bg-bg p-3 sm:w-[420px]"
        >
          <BlockRenderer
            blocks={item.blocks}
            depth={depth + 1}
            onFormSubmit={onFormSubmit}
            onFollowUp={onFollowUp}
            onOpenDocument={onOpenDocument}
          />
        </div>
      ))}
    </div>
  );
}
