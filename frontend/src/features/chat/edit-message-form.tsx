import { useState, type KeyboardEvent } from 'react';

import { Button } from '@/components/ui/button';

export function EditMessageForm({
  initial,
  onCancel,
  onSend,
}: {
  initial: string;
  onCancel: () => void;
  onSend: (content: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const trimmed = value.trim();

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Escape') onCancel();
  };

  return (
    <div className="rounded-lg border border-line bg-subtle p-2">
      <textarea
        aria-label="Edit message"
        autoFocus
        rows={3}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        className="w-full resize-y bg-transparent px-1 py-0.5 text-[15px] leading-[1.6] text-ink outline-none"
      />
      <div className="mt-1 flex justify-end gap-2">
        <Button size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={trimmed === ''}
          onClick={() => onSend(trimmed)}
        >
          Send
        </Button>
      </div>
    </div>
  );
}
