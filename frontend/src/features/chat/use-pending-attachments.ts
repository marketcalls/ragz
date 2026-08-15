import { useCallback, useEffect, useRef, useState } from 'react';

// Mirrors the backend's `interactive_upload_mb` cap (50 MB) enforced by
// `POST /api/v1/chats/{chat_id}/attachments`. This is a client-side UX
// guard only -- rejecting oversize files before they're even held in
// memory -- the server remains the source of truth and re-checks on upload.
export const MAX_ATTACHMENT_MB = 50;
export const MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024;

export interface PendingAttachment {
  id: string; // client-local id, not the server attachment id (that doesn't exist until upload-at-send)
  file: File;
  previewUrl: string | null; // objectURL for image files only, revoked on remove/clear/unmount
}

let fallbackIdSeq = 0;
function nextLocalId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  fallbackIdSeq += 1;
  return `pending-${fallbackIdSeq}`;
}

/**
 * Holds picked files locally (no upload) so attaching works before a chat
 * exists -- the caller uploads them at send time once a real chat_id is
 * known. See `use-send-message.ts` for the upload-at-send orchestration.
 */
export function usePendingAttachments() {
  const [files, setFiles] = useState<PendingAttachment[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Mirrors `files` synchronously so the unmount cleanup can revoke every
  // outstanding object URL without depending on the latest render's closure.
  const filesRef = useRef<PendingAttachment[]>([]);
  filesRef.current = files;

  const addFiles = useCallback((incoming: File[]) => {
    const accepted: PendingAttachment[] = [];
    let rejection: string | null = null;
    for (const file of incoming) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        rejection = `"${file.name}" is larger than ${MAX_ATTACHMENT_MB} MB and was not attached.`;
        continue;
      }
      accepted.push({
        id: nextLocalId(),
        file,
        previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
      });
    }
    if (accepted.length > 0) setFiles((prev) => [...prev, ...accepted]);
    setError(rejection);
  }, []);

  const remove = useCallback((id: string) => {
    setFiles((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((p) => p.id !== id);
    });
  }, []);

  const clear = useCallback(() => {
    setFiles((prev) => {
      for (const p of prev) if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
      return [];
    });
  }, []);

  const clearError = useCallback(() => setError(null), []);

  // Revoke any object URLs still outstanding when the composer unmounts
  // (e.g. navigating away without sending).
  useEffect(() => {
    return () => {
      for (const p of filesRef.current) if (p.previewUrl) URL.revokeObjectURL(p.previewUrl);
    };
  }, []);

  return { files, error, addFiles, remove, clear, clearError };
}
