import { ArrowUp, Square } from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';

export function ChatInput({
  onSend,
  disabled,
  busy = false,
  onStop,
  placeholder = 'Ask about your documents…',
}: {
  onSend: (content: string) => void;
  disabled: boolean;
  busy?: boolean;
  onStop?: () => void;
  placeholder?: string;
}) {
  const [value, setValue] = useState('');
  const boxRef = useRef<HTMLTextAreaElement>(null);

  const submit = (): void => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content);
    setValue('');
    if (boxRef.current) boxRef.current.style.height = 'auto';
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="mx-auto w-full max-w-thread px-4 pb-4">
      <div className="flex items-end gap-2 rounded-xl border border-line bg-bg p-2 shadow-soft">
        <textarea
          ref={boxRef}
          aria-label="Message"
          rows={1}
          value={value}
          placeholder={placeholder}
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = 'auto';
            e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
          }}
          onKeyDown={onKeyDown}
          className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1 text-[15px] text-ink outline-none placeholder:text-muted"
        />
        {busy ? (
          <button
            type="button"
            aria-label="Stop generating"
            onClick={onStop}
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-ink text-bg"
          >
            <Square className="h-3 w-3 fill-current" aria-hidden />
          </button>
        ) : (
          <button
            type="button"
            aria-label="Send"
            disabled={disabled || value.trim() === ''}
            onClick={submit}
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-ink text-bg disabled:opacity-40"
          >
            <ArrowUp className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}
