import { type ReactNode } from 'react';

export function UserMessage({ content, footer }: { content: string; footer?: ReactNode }) {
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-subtle px-3 py-2 text-[15px] leading-[1.6] text-ink">
        {content}
      </div>
      {footer}
    </div>
  );
}
