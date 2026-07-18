import { ChevronLeft, ChevronRight, Copy, Pencil, RotateCcw } from 'lucide-react';

import { toast } from '@/components/ui/toaster';

import { branchKeyOf, type PathEntry } from './tree';

function ActionButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded-sm p-1 text-muted hover:bg-subtle hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

export function MessageActions({
  entry,
  disabled,
  onSelectSibling,
  onEdit,
  onRegenerate,
}: {
  entry: PathEntry;
  disabled: boolean;
  onSelectSibling: (branchKey: string, id: string) => void;
  onEdit?: () => void;
  onRegenerate?: () => void;
}) {
  const { message, siblings, position } = entry;
  const branchKey = branchKeyOf(message);
  const prevId = position > 0 ? siblings[position - 1] : undefined;
  const nextId = position < siblings.length - 1 ? siblings[position + 1] : undefined;

  const copy = async (): Promise<void> => {
    await navigator.clipboard.writeText(message.content);
    toast('Copied to clipboard');
  };

  return (
    <div
      className="mt-1 flex items-center gap-0.5 opacity-60 focus-within:opacity-100 hover:opacity-100"
      aria-label="Message actions"
    >
      {siblings.length > 1 ? (
        <span className="mr-1 flex items-center gap-0.5">
          <ActionButton
            label="Previous version"
            disabled={disabled || !prevId}
            onClick={() => prevId && onSelectSibling(branchKey, prevId)}
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          </ActionButton>
          <span className="text-[11px] tabular-nums text-muted">
            {position + 1}/{siblings.length}
          </span>
          <ActionButton
            label="Next version"
            disabled={disabled || !nextId}
            onClick={() => nextId && onSelectSibling(branchKey, nextId)}
          >
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          </ActionButton>
        </span>
      ) : null}
      <ActionButton label="Copy message" onClick={() => void copy()}>
        <Copy className="h-3.5 w-3.5" aria-hidden />
      </ActionButton>
      {onEdit ? (
        <ActionButton label="Edit message" disabled={disabled} onClick={onEdit}>
          <Pencil className="h-3.5 w-3.5" aria-hidden />
        </ActionButton>
      ) : null}
      {onRegenerate ? (
        <ActionButton label="Regenerate response" disabled={disabled} onClick={onRegenerate}>
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        </ActionButton>
      ) : null}
    </div>
  );
}
