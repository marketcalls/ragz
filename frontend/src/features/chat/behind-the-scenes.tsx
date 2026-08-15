import { ChevronDown, Globe, Search } from 'lucide-react';
import { useState } from 'react';

import type { AgentStepInfo, ToolResultInfo, ToolResultItem } from '@/api/types';
import { cn } from '@/lib/cn';

// Design 2026-08-15: a collapsible "Behind the scenes" section under an
// assistant message showing the agent loop's tool calls for THIS turn.
// Live-only -- agentSteps/toolResults are captured off SSE frames and never
// persisted (chat/service.py doesn't write them to Message), so a reloaded
// history shows no section at all. That's an accepted gap, not a bug: the
// underlying citations/answer are unaffected.
//
// Web-search results render as openui-inspired source cards: favicon +
// hostname header, title link, and a short snippet of context.

function isHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

// Favicon for a result's hostname. Uses DuckDuckGo's icon service (DDG is the
// default web-search provider, so it's the consistent, privacy-respecting
// choice), rel=no-referrer so the chat URL never leaks, and falls back to a
// neutral globe glyph the moment the icon 404s or the host is blank/non-http.
function Favicon({ hostname }: { hostname: string }) {
  const [failed, setFailed] = useState(false);
  if (!hostname || failed) {
    return <Globe className="h-4 w-4 shrink-0 text-muted" aria-hidden />;
  }
  return (
    <img
      src={`https://icons.duckduckgo.com/ip3/${hostname}.ico`}
      alt=""
      aria-hidden
      width={16}
      height={16}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="h-4 w-4 shrink-0 rounded-sm"
    />
  );
}

function SourceCard({ result }: { result: ToolResultItem }) {
  const http = isHttpUrl(result.url);
  return (
    <li className="rounded-md border border-line bg-bg p-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted">
        <Favicon hostname={http ? result.source : ''} />
        <span className="min-w-0 truncate">{result.source || 'web'}</span>
      </div>
      {http ? (
        <a
          href={result.url}
          target="_blank"
          rel="noreferrer noopener"
          className="block text-[12.5px] font-medium text-ink underline-offset-2 hover:underline"
        >
          {result.title}
        </a>
      ) : (
        // Non-http(s) url from the search provider: render as plain text, never
        // as a link (no javascript:/data: hrefs reach the DOM).
        <span className="block text-[12.5px] font-medium text-ink">{result.title}</span>
      )}
      {result.snippet ? (
        <p className="mt-1 line-clamp-2 text-[12px] leading-snug text-secondary">
          {result.snippet}
        </p>
      ) : null}
    </li>
  );
}

function StepCard({ step, result }: { step: AgentStepInfo; result?: ToolResultInfo }) {
  const [expanded, setExpanded] = useState(false);
  const expandable = step.tool === 'web_search' && !!result && result.results.length > 0;
  return (
    <div className="rounded-md border border-line bg-raised">
      <button
        type="button"
        onClick={() => {
          if (expandable) setExpanded((v) => !v);
        }}
        aria-expanded={expandable ? expanded : undefined}
        disabled={!expandable}
        className={cn(
          'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px] text-secondary',
          expandable && 'cursor-pointer hover:text-ink',
        )}
      >
        {step.tool === 'web_search' ? (
          <Globe className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
        ) : (
          <Search className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
        )}
        <span className="min-w-0 flex-1 truncate">
          Called the <span className="font-medium text-ink">{step.tool}</span> tool
        </span>
        {expandable ? (
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-150 ease-out',
              expanded && 'rotate-180',
            )}
            aria-hidden
          />
        ) : null}
      </button>
      {expandable && expanded ? (
        <ul className="grid gap-1.5 border-t border-line p-1.5">
          {result?.results.map((r, i) => <SourceCard key={`${r.url}-${i}`} result={r} />)}
        </ul>
      ) : null}
    </div>
  );
}

export function BehindTheScenes({
  steps,
  toolResults,
}: {
  steps: AgentStepInfo[];
  toolResults: ToolResultInfo[];
}) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
  return (
    <div className="mb-3 rounded-md border border-line bg-bg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[12px] font-medium text-secondary hover:text-ink"
      >
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 shrink-0 transition-transform duration-150 ease-out',
            open && 'rotate-180',
          )}
          aria-hidden
        />
        Behind the scenes
      </button>
      {open ? (
        <div className="space-y-1.5 border-t border-line p-2">
          {steps.map((step) => (
            <StepCard
              key={step.n}
              step={step}
              result={toolResults.find((r) => r.n === step.n && r.tool === step.tool)}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
