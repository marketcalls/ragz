import { useState, type ReactNode } from 'react';

import { Markdown } from '@/components/markdown/markdown';

import { CitationProvider } from './citation-context';
import { NoAnswerNotice } from './no-answer-notice';
import { SourcePanel, type SourceChipData } from './source-panel';

export function AssistantMessage({
  content,
  sources,
  noAnswer = false,
  stopped = false,
  grounding = 'documents',
  validationFailed = false,
  footer,
}: {
  content: string;
  sources: SourceChipData[];
  noAnswer?: boolean;
  stopped?: boolean;
  grounding?: string;
  validationFailed?: boolean;
  footer?: ReactNode;
}) {
  const [highlightedN, setHighlightedN] = useState<number | null>(null);
  return (
    <div>
      {grounding === 'general' ? (
        <span className="mb-2 inline-flex items-center rounded-full bg-subtle px-2 py-0.5 text-xs text-secondary">
          General knowledge — not from your documents
        </span>
      ) : null}
      <CitationProvider onCitationClick={setHighlightedN} sources={sources}>
        <Markdown content={content} />
      </CitationProvider>
      {noAnswer ? (
        <NoAnswerNotice />
      ) : (
        <SourcePanel sources={sources} highlightedN={highlightedN} onSelect={setHighlightedN} />
      )}
      {noAnswer && sources.length > 0 ? (
        <div className="mt-2">
          <p className="mb-1 text-[12px] text-muted">Nearest sources</p>
          <SourcePanel sources={sources} highlightedN={highlightedN} onSelect={setHighlightedN} />
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
