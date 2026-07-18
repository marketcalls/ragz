import { Check, Copy } from 'lucide-react';
import { useState, type ReactNode } from 'react';

function textOf(node: ReactNode): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (node && typeof node === 'object' && 'props' in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  }
  return '';
}

export function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(textOf(children).replace(/\n$/, ''));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="group relative my-2 rounded-md border border-line bg-subtle">
      <button
        type="button"
        aria-label="Copy code"
        onClick={() => void copy()}
        className="absolute right-2 top-2 rounded-sm p-1 text-muted hover:bg-raised hover:text-ink"
      >
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      </button>
      <pre className="overflow-x-auto p-3 font-mono text-[13px] leading-relaxed text-ink">
        {children}
      </pre>
    </div>
  );
}
