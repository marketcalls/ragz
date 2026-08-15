import { File as FileIcon, X } from 'lucide-react';

import type { PendingAttachment } from './use-pending-attachments';

// Preview/remove cards for files picked but not yet uploaded (upload happens
// at send time -- see `use-send-message.ts`). Rounded-square thumbnail cards
// (~64px, ChatGPT pattern): image files show the actual preview
// (`object-cover`); everything else shows a file icon + filename.
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
        <li key={f.id} className="group relative">
          <div className="h-16 w-16 overflow-hidden rounded-xl border border-line bg-subtle">
            {f.previewUrl ? (
              <img src={f.previewUrl} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-1.5 text-center">
                <FileIcon className="h-5 w-5 shrink-0 text-muted" aria-hidden />
                <span className="w-full truncate text-[10px] leading-tight text-secondary">
                  {f.file.name}
                </span>
              </div>
            )}
          </div>
          <button
            type="button"
            aria-label={`Remove ${f.file.name}`}
            onClick={() => onRemove(f.id)}
            className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-line bg-bg text-muted opacity-0 shadow-soft transition-opacity duration-150 ease-out hover:text-ink focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent group-hover:opacity-100"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  );
}
