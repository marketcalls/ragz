import { SearchX } from 'lucide-react';

export function NoAnswerNotice() {
  return (
    <div className="mt-2 flex items-center gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[13px] text-secondary">
      <SearchX className="h-4 w-4 shrink-0 text-muted" aria-hidden />
      <span>No confident answer in this workspace's documents for that question.</span>
    </div>
  );
}
