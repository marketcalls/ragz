import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api } from '@/api/client';
import { problemDetail } from '@/features/auth/mutations';

export type AttachmentImageStatus = 'loading' | 'success' | 'error';

/** Thrown when GET /chats/{id}/attachments/{id}/content returns a non-2xx --
 *  carries the HTTP status so a denied/missing attachment degrades to the
 *  existing chip instead of a broken frame. Mirrors DocumentFileError. */
export class AttachmentImageError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'AttachmentImageError';
    this.status = status;
  }
}

function useAttachmentImageBlob(
  chatId: string | null,
  attachmentId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['attachment-image', chatId, attachmentId],
    enabled: enabled && chatId !== null && attachmentId !== null,
    // Chat-ownership denial / not-found is deterministic for a given
    // user+attachment -- retrying on a timer just repeats the same 404.
    retry: false,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        '/api/v1/chats/{chat_id}/attachments/{attachment_id}/content',
        {
          params: {
            path: { chat_id: chatId as string, attachment_id: attachmentId as string },
          },
          parseAs: 'blob',
        },
      );
      if (error) throw new AttachmentImageError(response.status, problemDetail(error));
      return data;
    },
  });
}

/** Fetches a chat image attachment's bytes (ownership-scoped) and exposes it
 *  as an object URL for an inline thumbnail. Keyed by (chatId, attachmentId)
 *  via TanStack Query; the object URL is created/revoked in an effect keyed on
 *  the Blob identity -- cleaned up on unmount and whenever the blob changes, so
 *  a transcript never leaks a previous attachment's blob URL. Mirrors
 *  `useDocumentFile`. */
export function useAttachmentImage(
  chatId: string | null,
  attachmentId: string | null,
  enabled = true,
): { objectUrl: string | null; status: AttachmentImageStatus } {
  const query = useAttachmentImageBlob(chatId, attachmentId, enabled);
  const blob = query.data ?? null;
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!blob) {
      setObjectUrl(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);

  const status: AttachmentImageStatus = query.isError
    ? 'error'
    : blob && objectUrl
      ? 'success'
      : 'loading';

  return { objectUrl, status };
}
