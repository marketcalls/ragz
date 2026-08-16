import { useState, type ReactNode } from 'react';

import type { AgentStepInfo, Block, ToolResultInfo } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';

import { BehindTheScenes } from './behind-the-scenes';
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
  agentSteps = [],
  toolResults = [],
  footer,
  onOpenDocument,
  onFormSubmit,
  onFollowUp,
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
  // "Behind the scenes" (design 2026-08-15): live-turn-only -- agent steps
  // aren't persisted, so these are always [] for a message rendered from
  // chat history (chat-page.tsx never passes them). Only the live streaming
  // message (streaming-message.tsx) does.
  agentSteps?: AgentStepInfo[];
  toolResults?: ToolResultInfo[];
  footer?: ReactNode;
  // Citation -> source-document drawer (chip click and inline [n] marker
  // click both route through here; web citations are excluded below).
  onOpenDocument?: (source: SourceChipData) => void;
  // Phase 3 (interactive in-chat forms): a `form` block's submit composes a
  // normal chat message and sends it through the SAME send path as the
  // composer (wired from chat-page.tsx). Absent (e.g. read-only contexts) --
  // BlockRenderer/FormBlockView render the form with submit disabled.
  onFormSubmit?: (message: string) => void;
  // Task 6 (generative-UI design 2026-08-16, §6): a `follow_ups` chip's click
  // sends its text as a normal chat message through the SAME send path --
  // mirrors onFormSubmit exactly (same value passed from chat-page.tsx).
  onFollowUp?: (message: string) => void;
}) {
  const [highlightedN, setHighlightedN] = useState<number | null>(null);

  // Fix C (generative-UI presentation polish): a `source_refs` block already
  // renders the same sources as cards -- the legacy SourcePanel chips below
  // would just duplicate them, so suppress SourcePanel whenever one is present.
  const hasSourceRefsBlock = (blocks ?? []).some((b) => b.type === 'source_refs');

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
      <BehindTheScenes steps={agentSteps} toolResults={toolResults} />
      <CitationProvider onCitationClick={handleCitationClick} sources={sources}>
        <Markdown content={content} />
      </CitationProvider>
      {blocks && blocks.length > 0 ? (
        <BlockRenderer
          blocks={blocks}
          onFormSubmit={onFormSubmit}
          onFollowUp={onFollowUp}
          onOpenDocument={onOpenDocument}
        />
      ) : null}
      {noAnswer ? (
        <NoAnswerNotice />
      ) : !hasSourceRefsBlock ? (
        <SourcePanel
          sources={sources}
          highlightedN={highlightedN}
          onSelect={setHighlightedN}
          onOpenDocument={onOpenDocument}
        />
      ) : null}
      {noAnswer && sources.length > 0 ? (
        <div className="mt-2">
          <p className="mb-1 text-[12px] text-muted">Nearest sources</p>
          {!hasSourceRefsBlock ? (
            <SourcePanel
              sources={sources}
              highlightedN={highlightedN}
              onSelect={setHighlightedN}
              onOpenDocument={onOpenDocument}
            />
          ) : null}
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
