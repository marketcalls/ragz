import type { ButtonsBlock as ButtonsBlockT } from '@/api/types';
import { Button } from '@/components/ui/button';

// T-C (2026-08-16): a row of action buttons. Clicking a button sends its
// `message` as a new chat turn through the SAME send path as follow_ups
// chips (follow-ups.tsx) -- onFollowUp is deliberately reused rather than a
// new handler prop. Absent -- buttons still render but are disabled,
// mirroring FollowUps/FormBlockView's "no send path -> disabled, never a
// silent no-op" contract.
export function ActionButtons({
  block,
  onFollowUp,
}: {
  block: ButtonsBlockT;
  onFollowUp?: (message: string) => void;
}) {
  if (block.items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {block.items.map((item, i) => (
        <Button
          key={i}
          type="button"
          variant={item.variant === 'secondary' ? 'secondary' : 'primary'}
          disabled={!onFollowUp}
          onClick={() => onFollowUp?.(item.message)}
        >
          {item.label}
        </Button>
      ))}
    </div>
  );
}
