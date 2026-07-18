import { useCitationClick } from './citation-context';

export function CitationChip({ n }: { n: string | number }) {
  const onCitationClick = useCitationClick();
  const num = Number(n);
  return (
    <button
      type="button"
      aria-label={`Citation ${num}`}
      onClick={() => onCitationClick(num)}
      className="mx-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-sm bg-accent-soft px-1 align-baseline text-[11px] font-medium text-accent-on-soft hover:opacity-80"
    >
      {num}
    </button>
  );
}
