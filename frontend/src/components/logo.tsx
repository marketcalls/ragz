import { cn } from '@/lib/cn';

/**
 * Ragz brandmark — a solid synthesis stem with two stepped "source" arms and a
 * detached generated-answer node: ranked retrieval feeding generation, the
 * shape of RAG. Single-color via `currentColor`, so it inherits the surrounding
 * text color and tracks white-label accent overrides when placed on
 * `text-accent`. Decorative next to the "Ragz" wordmark, hence `aria-hidden`.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={cn('text-accent', className)}
      aria-hidden
      focusable="false"
    >
      {/* synthesis stem */}
      <rect x="3.5" y="3" width="4.25" height="18" rx="2.125" />
      {/* source arm 1 — top, widest (highest-ranked) */}
      <rect x="8.25" y="3" width="11" height="4.25" rx="2.125" />
      {/* source arm 2 — narrower */}
      <rect x="8.25" y="9.875" width="7.5" height="4.25" rx="2.125" />
      {/* generated-answer node */}
      <circle cx="16.75" cy="18.25" r="2.75" />
    </svg>
  );
}

export default Logo;
