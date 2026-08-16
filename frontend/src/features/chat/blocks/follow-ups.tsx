import type { FollowUpsBlock as FollowUpsBlockT } from '@/api/types';

// Task 6 (generative-UI design 2026-08-16, §6): follow-up suggestion chips
// under an assistant message. Clicking a chip sends its text as a new chat
// turn -- mirrors FormBlockView's onSubmit exactly (same send path, same
// "absent -> render disabled, never a silent no-op button" contract).
export function FollowUps({
  block,
  onFollowUp,
}: {
  block: FollowUpsBlockT;
  // Absent in contexts with no send path -- chips still render but are
  // disabled, mirroring FormBlockView's canSubmit gating.
  onFollowUp?: (message: string) => void;
}) {
  if (block.items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {block.items.map((item, i) => (
        <button
          key={i}
          type="button"
          disabled={!onFollowUp}
          onClick={() => onFollowUp?.(item)}
          className="rounded-full border border-line bg-bg px-3 py-1.5 text-[13px] font-medium text-ink transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent-on-soft disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line disabled:hover:bg-bg disabled:hover:text-ink"
        >
          {item}
        </button>
      ))}
    </div>
  );
}
