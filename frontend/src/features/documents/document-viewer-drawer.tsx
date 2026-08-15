import * as DialogPrimitive from '@radix-ui/react-dialog';
import { Download, ExternalLink, X } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';

import { useDocumentFile } from './document-file';

function isViewableMime(mime: string | null): boolean {
  if (!mime) return false;
  return mime === 'application/pdf' || mime.startsWith('image/') || mime.startsWith('text/');
}

function downloadBlobUrl(objectUrl: string, filename: string): void {
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  a.click();
}

function DownloadButton({ objectUrl, filename }: { objectUrl: string; filename: string }) {
  return (
    <Button variant="secondary" size="sm" onClick={() => downloadBlobUrl(objectUrl, filename)}>
      <Download className="h-3.5 w-3.5" aria-hidden />
      Download
    </Button>
  );
}

function CenteredMessage({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      {children}
    </div>
  );
}

/** Right-side drawer (in-app, not a new tab -- chat stays visible behind it)
 * that previews the ACL-gated original file for a citation. Page-level open
 * only: `#page=N` jumps the browser's native PDF viewer to the cited page --
 * there's no bbox/passage-highlight data yet (future project). */
export function DocumentViewerDrawer({
  documentId,
  page,
  filename,
  version,
  onClose,
}: {
  documentId: string;
  page: number;
  filename: string;
  version?: number;
  onClose: () => void;
}) {
  const { objectUrl, mimeType, status } = useDocumentFile(documentId);
  const pageSrc = objectUrl ? `${objectUrl}#page=${page}` : undefined;
  const viewable = status === 'success' && objectUrl !== null && isViewableMime(mimeType);

  return (
    <DialogPrimitive.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-scrim data-[state=open]:animate-overlay-in data-[state=closed]:animate-overlay-out" />
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-2xl flex-col border-l border-line bg-bg shadow-md focus:outline-none data-[state=open]:animate-drawer-in data-[state=closed]:animate-drawer-out">
          <DialogPrimitive.Description className="sr-only">
            Previewing {filename}
          </DialogPrimitive.Description>
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0">
              <DialogPrimitive.Title asChild>
                <h2 className="truncate text-[14px] font-medium text-ink">{filename}</h2>
              </DialogPrimitive.Title>
              <p className="text-[12px] text-muted">
                {version != null ? `v${version} · ` : ''}p. {page}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {objectUrl ? (
                <>
                  <DownloadButton objectUrl={objectUrl} filename={filename} />
                  <a
                    href={pageSrc}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-[12px] text-secondary transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink"
                  >
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                    Open in new tab
                  </a>
                </>
              ) : null}
              <DialogPrimitive.Close
                aria-label="Close"
                className="rounded-sm p-1.5 text-secondary transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink"
              >
                <X className="h-4 w-4" aria-hidden />
              </DialogPrimitive.Close>
            </div>
          </div>
          <div className="flex-1 overflow-auto">
            {status === 'loading' ? (
              <CenteredMessage>
                <Spinner label="Loading document…" />
              </CenteredMessage>
            ) : status === 'forbidden' ? (
              <CenteredMessage>
                <p role="alert" className="text-[13px] text-secondary">
                  You don&apos;t have access to this document.
                </p>
              </CenteredMessage>
            ) : status === 'not-found' ? (
              <CenteredMessage>
                <p role="alert" className="text-[13px] text-secondary">
                  Document not found.
                </p>
              </CenteredMessage>
            ) : status === 'error' ? (
              <CenteredMessage>
                <p role="alert" className="text-[13px] text-secondary">
                  Something went wrong loading this document.
                </p>
              </CenteredMessage>
            ) : viewable && pageSrc ? (
              <iframe title={filename} src={pageSrc} className="h-full w-full border-0" />
            ) : (
              <CenteredMessage>
                <p className="text-[13px] font-medium text-ink">{filename}</p>
                <p className="text-[13px] text-secondary">This file type can&apos;t be previewed</p>
                {objectUrl ? (
                  <div className="mt-1 flex items-center gap-2">
                    <DownloadButton objectUrl={objectUrl} filename={filename} />
                    <a
                      href={pageSrc}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-[12px] text-secondary transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink"
                    >
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                      Open in new tab
                    </a>
                  </div>
                ) : null}
              </CenteredMessage>
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
