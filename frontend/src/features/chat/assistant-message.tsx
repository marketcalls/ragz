import { useState, type ReactNode } from 'react';

import type { Block } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';

import { BlockRenderer } from './blocks/block-renderer';
import { CitationProvider } from './citation-context';
import { NoAnswerNotice } from './no-answer-notice';
import { SourcePanel, type SourceChipData } from './source-panel';

export function AssistantMessage({
  content,
  sources,
  blocks,
  noAnswer = false,
  stopped = false,
  grounding = 'documents',
  validationFailed = false,
  footer,
  onOpenDocument,
}: {
  content: string;
  sources: SourceChipData[];
  // Phase 2 (in-chat generative UI): live (SSE `blocks` frame) or persisted
  // (MessageNode.blocks) -- both render identically via BlockRenderer.
  blocks?: Block[] | null;
  noAnswer?: boolean;
  stopped?: boolean;
  grounding?: string;
  validationFailed?: boolean;
  footer?: ReactNode;
  // Citation -> source-document drawer (chip click and inline [n] marker
  // click both route through here; web citations are excluded below).
  onOpenDocument?: (source: SourceChipData) => void;
}) {
  const [highlightedN, setHighlightedN] = useState<number | null>(null);

  // Inline `[n]` markers only carry the marker number (CitationChip has no
  // access to the full source list) -- resolve back to the source here and
  // reuse the same open-drawer path the chip buttons use, so both entry
  // points behave identically.
  const handleCitationClick = (n: number): void => {
    setHighlightedN(n);
    const source = sources.find((s) => s.marker === n);
    if (source && !source.url) onOpenDocument?.(source);
  };

  return (
    <div>
      {grounding === 'general' ? (
        <span className="mb-2 inline-flex items-center rounded-full bg-subtle px-2 py-0.5 text-xs text-secondary">
          General knowledge — not from your documents
        </span>
      ) : null}
      <CitationProvider onCitationClick={handleCitationClick} sources={sources}>
        <Markdown content={content} />
      </CitationProvider>
      {blocks && blocks.length > 0 ? <BlockRenderer blocks={blocks} /> : null}
      {noAnswer ? (
        <NoAnswerNotice />
      ) : (
        <SourcePanel
          sources={sources}
          highlightedN={highlightedN}
          onSelect={setHighlightedN}
          onOpenDocument={onOpenDocument}
        />
      )}
      {noAnswer && sources.length > 0 ? (
        <div className="mt-2">
          <p className="mb-1 text-[12px] text-muted">Nearest sources</p>
          <SourcePanel
            sources={sources}
            highlightedN={highlightedN}
            onSelect={setHighlightedN}
            onOpenDocument={onOpenDocument}
          />
        </div>
      ) : null}
      {stopped ? (
        <span className="mt-2 inline-flex items-center rounded-full bg-subtle px-2 py-0.5 text-xs text-secondary">
          Stopped
        </span>
      ) : null}
      {validationFailed ? (
        <span className="mb-2 ml-1 inline-flex items-center rounded-full bg-warning/20 px-2 py-0.5 text-xs text-warning">
          Not re-verified after revision
        </span>
      ) : null}
      {footer}
    </div>
  );
}
