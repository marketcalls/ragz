import { FileText, Globe } from 'lucide-react';

import { cn } from '@/lib/cn';

function safeHostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** Minimal chip shape: SSE SourceRef[] is assignable; persisted CitationOut[]
 *  gets mapped to it (filename resolved via the documents list, Task 10).
 *  Task 11 (D7): `url` set only for web-search hits -- the chip then renders
 *  a hostname + globe glyph + "open" link instead of the doc filename/page. */
export interface SourceChipData {
  marker: number;
  document_id: string;
  filename: string;
  page: number;
  snippet?: string;
  section?: string | null;
  version?: number;
  url?: string | null;
}

export function SourcePanel({
  sources,
  highlightedN,
  onSelect,
  onOpenDocument,
}: {
  sources: SourceChipData[];
  highlightedN?: number | null;
  onSelect?: (n: number) => void;
  // Document citations only (Task: citation -> source-document drawer) --
  // web citations (source.url set) keep their separate "open" external link
  // below and never open the in-app viewer.
  onOpenDocument?: (source: SourceChipData) => void;
}) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5" role="list" aria-label="Sources">
      {sources.map((source) => (
        <div key={source.marker} role="listitem" className="inline-flex items-center">
          <button
            type="button"
            onClick={() => {
              onSelect?.(source.marker);
              if (!source.url) onOpenDocument?.(source);
            }}
            title={source.snippet}
            aria-label={`Source ${source.marker}: ${source.filename}, page ${source.page}`}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-[12px] text-secondary hover:text-ink',
              highlightedN === source.marker && 'border-accent text-ink',
              source.url && 'border-dashed border-accent/60',
            )}
          >
            <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-accent-soft px-0.5 text-[10px] font-medium text-accent-on-soft">
              {source.marker}
            </span>
            {source.url ? (
              <>
                <Globe className="h-3 w-3 shrink-0 text-muted" aria-hidden />
                <span className="max-w-[220px] truncate">{source.filename}</span>
                <span className="text-muted">· {safeHostname(source.url)}</span>
              </>
            ) : (
              <>
                <FileText className="h-3 w-3 text-muted" aria-hidden />
                <span className="max-w-[220px] truncate">{source.filename}</span>
                {source.version != null ? (
                  <span className="text-muted">· v{source.version}</span>
                ) : null}
                <span className="text-muted">· p. {source.page}</span>
                {source.section ? (
                  <span className="max-w-[180px] truncate text-muted">· {source.section}</span>
                ) : null}
              </>
            )}
          </button>
          {source.url ? (
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-1 text-[11px] text-accent underline"
            >
              open
            </a>
          ) : null}
        </div>
      ))}
    </div>
  );
}
