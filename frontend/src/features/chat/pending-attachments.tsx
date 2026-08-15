import { File as FileIcon, X } from 'lucide-react';

import type { PendingAttachment } from './use-pending-attachments';

// Preview/remove chips for files picked but not yet uploaded (upload happens
// at send time -- see `use-send-message.ts`). Image files render a small
// thumbnail; everything else renders a generic file chip.
export function PendingAttachments({
  files,
  onRemove,
}: {
  files: PendingAttachment[];
  onRemove: (id: string) => void;
}) {
  if (files.length === 0) return null;

  return (
    <ul aria-label="Pending attachments" className="flex flex-wrap gap-2 px-4 pb-2">
      {files.map((f) => (
        <li
          key={f.id}
          className="flex max-w-[220px] items-center gap-1.5 rounded-full border border-line bg-subtle py-1 pl-1 pr-2 text-[12px] text-secondary"
        >
          {f.previewUrl ? (
            <img
              src={f.previewUrl}
              alt=""
              className="h-5 w-5 shrink-0 rounded-full object-cover"
            />
          ) : (
            <FileIcon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          )}
          <span className="truncate">{f.file.name}</span>
          <button
            type="button"
            aria-label={`Remove ${f.file.name}`}
            onClick={() => onRemove(f.id)}
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-muted transition-colors duration-150 ease-out hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  );
}
