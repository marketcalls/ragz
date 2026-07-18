import { useState, type ReactNode } from 'react';

import { Markdown } from '@/components/markdown/markdown';

import { CitationProvider } from './citation-context';
import { NoAnswerNotice } from './no-answer-notice';
import { SourcePanel, type SourceChipData } from './source-panel';

export function AssistantMessage({
  content,
  sources,
  noAnswer = false,
  footer,
}: {
  content: string;
  sources: SourceChipData[];
  noAnswer?: boolean;
  footer?: ReactNode;
}) {
  const [highlightedN, setHighlightedN] = useState<number | null>(null);
  return (
    <div>
      <CitationProvider onCitationClick={setHighlightedN}>
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
      {footer}
    </div>
  );
}
