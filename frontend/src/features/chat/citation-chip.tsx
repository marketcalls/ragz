import { useCitationClick, useCitationMeta } from './citation-context';

export function CitationChip({ n }: { n: string | number }) {
  const onCitationClick = useCitationClick();
  const num = Number(n);
  const meta = useCitationMeta(num);
  // "v2 · Section > Path" hover tooltip (CHAT-4); omitted when neither is known.
  const title =
    [meta?.version != null ? `v${meta.version}` : null, meta?.section ?? null]
      .filter(Boolean)
      .join(' · ') || undefined;
  return (
    <button
      type="button"
      aria-label={`Citation ${num}`}
      title={title}
      onClick={() => onCitationClick(num)}
      className="mx-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-sm bg-accent-soft px-1 align-baseline text-[11px] font-medium text-accent-on-soft hover:opacity-80"
    >
      {num}
    </button>
  );
}
