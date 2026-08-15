import { File as FileIcon, Image as ImageIcon } from 'lucide-react';
import { type ReactNode } from 'react';

import type { AttachmentOut } from '@/api/types';

// Transcript rendering (design 2026-08-15): the chat history read path
// (chat/schemas.py::MessageNode.attachments) only ever exposes metadata --
// id/kind/filename/mime/status, never bytes/storage_key -- and there is no
// authed image-fetch endpoint for chat attachments (only a POST upload
// route exists). So every attachment renders as a read-only chip (kind
// badge + filename), never a live `<img src>` thumbnail — that keeps this
// component from needing to guess at a URL that doesn't safely exist, and a
// source that can't render still degrades to a chip instead of a broken
// image.
function AttachmentChip({ attachment }: { attachment: AttachmentOut }) {
  const Icon = attachment.kind === 'image' ? ImageIcon : FileIcon;
  return (
    <li className="flex max-w-[220px] items-center gap-1.5 rounded-full border border-line bg-subtle py-1 pl-2 pr-2.5 text-[12px] text-secondary">
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
      <span className="truncate">{attachment.filename}</span>
    </li>
  );
}

export function UserMessage({
  content,
  attachments,
  footer,
}: {
  content: string;
  // Absent on live-turn echoes that haven't round-tripped through history
  // yet; null/[] once persisted with no attachments (see MessageNode).
  attachments?: AttachmentOut[] | null;
  footer?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-end">
      {attachments && attachments.length > 0 ? (
        <ul
          aria-label="Attachments"
          className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-1.5"
        >
          {attachments.map((a) => (
            <AttachmentChip key={a.id} attachment={a} />
          ))}
        </ul>
      ) : null}
      <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-subtle px-3 py-2 text-[15px] leading-[1.6] text-ink">
        {content}
      </div>
      {footer}
    </div>
  );
}
