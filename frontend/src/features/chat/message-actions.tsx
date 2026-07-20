import { ChevronLeft, ChevronRight, Copy, MessageSquare, Pencil, RotateCcw, ThumbsDown, ThumbsUp } from 'lucide-react';
import { useState } from 'react';

import type { FeedbackOut } from '@/api/types';
import { toast } from '@/components/ui/toaster';
import { cn } from '@/lib/cn';

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
  onSetFeedback,
  onClearFeedback,
}: {
  entry: PathEntry;
  disabled: boolean;
  onSelectSibling: (branchKey: string, id: string) => void;
  onEdit?: () => void;
  onRegenerate?: () => void;
  onSetFeedback?: (rating: 'up' | 'down', comment?: string) => void;
  onClearFeedback?: () => void;
}) {
  const { message, siblings, position } = entry;
  const feedback: FeedbackOut | null = message.feedback ?? null;
  const [commentDraft, setCommentDraft] = useState<string | null>(null);
  const branchKey = branchKeyOf(message);
  const prevId = position > 0 ? siblings[position - 1] : undefined;
  const nextId = position < siblings.length - 1 ? siblings[position + 1] : undefined;

  const copy = async (): Promise<void> => {
    await navigator.clipboard.writeText(message.content);
    toast('Copied to clipboard');
  };

  const toggleThumb = (rating: 'up' | 'down'): void => {
    if (feedback?.rating === rating) {
      onClearFeedback?.();
    } else {
      onSetFeedback?.(rating, feedback?.comment ?? undefined);
    }
  };

  const submitComment = (e: React.FormEvent): void => {
    e.preventDefault();
    if (feedback) onSetFeedback?.(feedback.rating as 'up' | 'down', commentDraft?.trim() || undefined);
    setCommentDraft(null);
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
          <span className="text-[11px] tabular-nums text-secondary">
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
      {onSetFeedback ? (
        <>
          <ActionButton label="Good answer" disabled={disabled} onClick={() => toggleThumb('up')}>
            <ThumbsUp className={cn('h-3.5 w-3.5', feedback?.rating === 'up' && 'fill-current')} aria-hidden />
          </ActionButton>
          <ActionButton label="Bad answer" disabled={disabled} onClick={() => toggleThumb('down')}>
            <ThumbsDown className={cn('h-3.5 w-3.5', feedback?.rating === 'down' && 'fill-current')} aria-hidden />
          </ActionButton>
          {feedback ? (
            commentDraft !== null ? (
              <form onSubmit={submitComment}>
                <input
                  autoFocus
                  value={commentDraft}
                  onChange={(e) => setCommentDraft(e.target.value)}
                  placeholder="Add a comment (optional)"
                  className="ml-1 rounded-sm border border-line bg-bg px-1 text-[12px]"
                />
              </form>
            ) : (
              <ActionButton
                label="Add a comment"
                disabled={disabled}
                onClick={() => setCommentDraft(feedback.comment ?? '')}
              >
                <MessageSquare className="h-3.5 w-3.5" aria-hidden />
              </ActionButton>
            )
          ) : null}
        </>
      ) : null}
    </div>
  );
}
