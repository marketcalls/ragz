import { ArrowUp, Square } from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';

import { ComposerControls } from './composer-controls';

export function ChatInput({
  onSend,
  disabled,
  busy = false,
  onStop,
  onSelectFiles,
  attachDisabled = false,
  webSearchAvailable,
  webSearch,
  onToggleWebSearch,
  placeholder = 'Ask about your documents…',
}: {
  onSend: (content: string) => void;
  disabled: boolean;
  busy?: boolean;
  onStop?: () => void;
  onSelectFiles: (files: File[]) => void;
  attachDisabled?: boolean;
  webSearchAvailable: boolean;
  webSearch: boolean;
  onToggleWebSearch: () => void;
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
      <div className="flex flex-col gap-1 rounded-xl border border-line bg-bg p-2 shadow-soft transition-shadow duration-150 ease-out focus-within:shadow-md">
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
          className="max-h-[200px] w-full resize-none bg-transparent px-2 py-1 text-[15px] text-ink outline-none placeholder:text-muted"
        />
        <div className="flex items-end justify-between gap-2">
          <ComposerControls
            onSelectFiles={onSelectFiles}
            disabled={attachDisabled}
            webSearchAvailable={webSearchAvailable}
            webSearch={webSearch}
            onToggleWebSearch={onToggleWebSearch}
          />
          {busy ? (
            <button
              type="button"
              aria-label="Stop generating"
              onClick={onStop}
              className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-ink text-bg transition-colors duration-150 ease-out"
            >
              <Square className="h-3 w-3 fill-current" aria-hidden />
            </button>
          ) : (
            <button
              type="button"
              aria-label="Send"
              disabled={disabled || value.trim() === ''}
              onClick={submit}
              className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-ink text-bg transition-colors duration-150 ease-out disabled:opacity-40"
            >
              <ArrowUp className="h-4 w-4" aria-hidden />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
