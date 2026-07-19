import { FileText } from 'lucide-react';

import { cn } from '@/lib/cn';

/** Minimal chip shape: SSE SourceRef[] is assignable; persisted CitationOut[]
 *  gets mapped to it (filename resolved via the documents list, Task 10). */
export interface SourceChipData {
  marker: number;
  document_id: string;
  filename: string;
  page: number;
  snippet?: string;
}

export function SourcePanel({
  sources,
  highlightedN,
  onSelect,
}: {
  sources: SourceChipData[];
  highlightedN?: number | null;
  onSelect?: (n: number) => void;
}) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5" role="list" aria-label="Sources">
      {sources.map((source) => (
        <div key={source.marker} role="listitem">
          <button
            type="button"
            onClick={() => onSelect?.(source.marker)}
            title={source.snippet}
            aria-label={`Source ${source.marker}: ${source.filename}, page ${source.page}`}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-[12px] text-secondary hover:text-ink',
              highlightedN === source.marker && 'border-accent text-ink',
            )}
          >
            <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-accent-soft px-0.5 text-[10px] font-medium text-accent-on-soft">
              {source.marker}
            </span>
            <FileText className="h-3 w-3 text-muted" aria-hidden />
            <span className="max-w-[220px] truncate">{source.filename}</span>
            <span className="text-muted">· p. {source.page}</span>
          </button>
        </div>
      ))}
    </div>
  );
}
