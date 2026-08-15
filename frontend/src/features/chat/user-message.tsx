import { File as FileIcon, Image as ImageIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import type { AttachmentOut } from '@/api/types';
import { Dialog, DialogContent } from '@/components/ui/dialog';

import { useAttachmentImage } from './use-attachment-image';

// Read-only transcript chip (kind badge + filename). Documents always render
// this; images fall back to it when there's no chat_id to fetch against, or
// when the image fetch fails.
function AttachmentChip({ attachment }: { attachment: AttachmentOut }) {
  const Icon = attachment.kind === 'image' ? ImageIcon : FileIcon;
  return (
    <li className="flex max-w-[220px] items-center gap-1.5 rounded-full border border-line bg-subtle py-1 pl-2 pr-2.5 text-[12px] text-secondary">
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
      <span className="truncate">{attachment.filename}</span>
    </li>
  );
}

// Sent image attachments render a real inline thumbnail fetched from the
// ownership-scoped content endpoint (design 2026-08-15). Clicking opens a
// lightbox with the full image. On load failure the attachment degrades to
// the read-only chip -- never a broken frame.
function SentImageAttachment({
  chatId,
  attachment,
}: {
  chatId: string;
  attachment: AttachmentOut;
}) {
  const { objectUrl, status } = useAttachmentImage(chatId, attachment.id);
  const [open, setOpen] = useState(false);

  if (status === 'error') return <AttachmentChip attachment={attachment} />;

  if (status === 'loading' || !objectUrl) {
    return (
      <li
        aria-label={`Loading ${attachment.filename}`}
        className="h-32 w-32 animate-pulse rounded-lg border border-line bg-subtle"
      />
    );
  }

  return (
    <li>
      <button
        type="button"
        aria-label={`Enlarge ${attachment.filename}`}
        onClick={() => setOpen(true)}
        className="block overflow-hidden rounded-lg border border-line transition-shadow duration-150 ease-out hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        <img
          src={objectUrl}
          alt={attachment.filename}
          className="max-h-[200px] max-w-[200px] object-cover"
        />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={attachment.filename} className="max-w-3xl">
          <img
            src={objectUrl}
            alt={attachment.filename}
            className="mx-auto max-h-[75vh] w-auto rounded-md object-contain"
          />
        </DialogContent>
      </Dialog>
    </li>
  );
}

export function UserMessage({
  content,
  attachments,
  chatId = null,
  footer,
}: {
  content: string;
  // Absent on live-turn echoes that haven't round-tripped through history
  // yet; null/[] once persisted with no attachments (see MessageNode).
  attachments?: AttachmentOut[] | null;
  // Needed to fetch image thumbnails; when null (e.g. pre-persist echoes)
  // images fall back to chips.
  chatId?: string | null;
  footer?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-end">
      {attachments && attachments.length > 0 ? (
        <ul
          aria-label="Attachments"
          className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-1.5"
        >
          {attachments.map((a) =>
            a.kind === 'image' && chatId ? (
              <SentImageAttachment key={a.id} chatId={chatId} attachment={a} />
            ) : (
              <AttachmentChip key={a.id} attachment={a} />
            ),
          )}
        </ul>
      ) : null}
      <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-subtle px-3 py-2 text-[15px] leading-[1.6] text-ink">
        {content}
      </div>
      {footer}
    </div>
  );
}
