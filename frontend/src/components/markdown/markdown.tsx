import type { ReactNode } from 'react';
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { CitationChip } from '@/features/chat/citation-chip';
import { remarkCitations } from '@/features/chat/remark-citations';

import { CodeBlock } from './code-block';

// Fenced code whose language is a diagram/chart type (```mermaid, ```pie,
// ...) is emitted by the synthesis model alongside a generative-UI `chart`
// block that already visualizes the same data -- rendering the raw fence too
// is both unreadable (raw mermaid/pie syntax as text) and redundant. Suppress
// it here rather than showing it as a normal code block.
const DIAGRAM_LANGS = new Set([
  'mermaid',
  'pie',
  'chart',
  'flowchart',
  'graph',
  'sequencediagram',
  'sequence',
  'gantt',
  'classdiagram',
  'statediagram',
  'erdiagram',
  'journey',
  'xychart',
  'xychart-beta',
  'quadrantchart',
  'mindmap',
  'timeline',
]);

// react-markdown renders a fence as <pre><code class="language-<lang>">…
// </code></pre> -- `pre`'s children is the (not-yet-invoked) <code> element,
// so its className can be inspected without executing the `code` component.
function languageOf(children: ReactNode): string | null {
  const child = Array.isArray(children) ? children[0] : children;
  if (child && typeof child === 'object' && 'props' in child) {
    const className = (child as { props?: { className?: string } }).props?.className;
    const match = className ? /language-(\S+)/.exec(className) : null;
    return match?.[1] ?? null;
  }
  return null;
}

function PreBlock({ children }: { children?: ReactNode }) {
  const lang = languageOf(children)?.toLowerCase() ?? null;
  if (lang && DIAGRAM_LANGS.has(lang)) return null;
  return <CodeBlock>{children}</CodeBlock>;
}

// 'citation-chip' comes from remarkCitations' hName; react-markdown accepts
// custom element names via a widened Components type.
const components = {
  'citation-chip': CitationChip,
  // model output must not auto-fetch remote URLs — classic RAG exfiltration
  // channel (OWASP LLM Top 10)
  img: () => null,
  pre: PreBlock,
  code: ({ children, className }: { children?: React.ReactNode; className?: string }) =>
    className ? (
      <code className={className}>{children}</code> // inside <pre>, CodeBlock styles it
    ) : (
      <code className="rounded-sm bg-subtle px-1 py-0.5 font-mono text-[13px]">{children}</code>
    ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline">
      {children}
    </a>
  ),
  p: (p: { children?: React.ReactNode }) => <p className="my-2 leading-relaxed">{p.children}</p>,
  ul: (p: { children?: React.ReactNode }) => <ul className="my-2 list-disc pl-5">{p.children}</ul>,
  ol: (p: { children?: React.ReactNode }) => <ol className="my-2 list-decimal pl-5">{p.children}</ol>,
  h1: (p: { children?: React.ReactNode }) => <h2 className="mt-4 mb-2 text-[16px] font-semibold">{p.children}</h2>,
  h2: (p: { children?: React.ReactNode }) => <h3 className="mt-4 mb-2 text-[15px] font-semibold">{p.children}</h3>,
  h3: (p: { children?: React.ReactNode }) => <h4 className="mt-3 mb-1 text-[14px] font-semibold">{p.children}</h4>,
  table: (p: { children?: React.ReactNode }) => (
    <div className="my-2 overflow-x-auto rounded-md border border-line">
      <table className="w-full text-[13px] tabular-nums">{p.children}</table>
    </div>
  ),
  thead: (p: { children?: React.ReactNode }) => (
    <thead className="sticky top-0 bg-raised text-left">{p.children}</thead>
  ),
  th: (p: { children?: React.ReactNode }) => (
    <th className="border-b border-line px-2.5 py-1.5 font-medium text-secondary">{p.children}</th>
  ),
  td: (p: { children?: React.ReactNode }) => (
    <td className="border-b border-line-faint px-2.5 py-1.5">{p.children}</td>
  ),
  tr: (p: { children?: React.ReactNode }) => <tr className="even:bg-raised">{p.children}</tr>,
  blockquote: (p: { children?: React.ReactNode }) => (
    <blockquote className="my-2 border-l-2 border-line-strong pl-3 text-secondary">
      {p.children}
    </blockquote>
  ),
} as Components;

export function Markdown({ content }: { content: string }) {
  return (
    <div className="text-[15px] leading-[1.6] text-ink">
      <ReactMarkdown
        skipHtml
        urlTransform={defaultUrlTransform}
        remarkPlugins={[remarkGfm, remarkCitations]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
