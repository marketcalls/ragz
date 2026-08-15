import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api } from '@/api/client';
import { problemDetail } from '@/features/auth/mutations';

export type DocumentFileStatus = 'loading' | 'success' | 'forbidden' | 'not-found' | 'error';

/** Thrown when GET /documents/{id}/file returns a non-2xx -- carries the HTTP
 *  status so the query error can be mapped to a specific, non-leaking
 *  `DocumentFileStatus` (403 -> "forbidden", 404 -> "not-found") instead of a
 *  generic failure. The endpoint is ACL-content-gated (Five Iron Rule #2): a
 *  denied user must see a clean message, not a broken frame. */
export class DocumentFileError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'DocumentFileError';
    this.status = status;
  }
}

function useDocumentFileBlob(documentId: string | null) {
  return useQuery({
    queryKey: ['document-file', documentId],
    enabled: documentId !== null,
    // ACL denial / not-found is deterministic for a given user+document --
    // retrying on a timer just repeats the same 403/404.
    retry: false,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/v1/documents/{document_id}/file', {
        params: { path: { document_id: documentId as string } },
        parseAs: 'blob',
      });
      if (error) throw new DocumentFileError(response.status, problemDetail(error));
      return data;
    },
  });
}

function statusFromError(error: unknown): 'forbidden' | 'not-found' | 'error' {
  if (error instanceof DocumentFileError) {
    if (error.status === 403) return 'forbidden';
    if (error.status === 404) return 'not-found';
  }
  return 'error';
}

/** Fetches the ACL-gated original file for a document (citation viewer) and
 *  exposes it as an object URL. Keyed by documentId (TanStack Query), so
 *  re-opening the same document within the cache window is instant.
 *
 *  The object URL itself is created/revoked in an effect keyed on the Blob
 *  identity -- separate from the query cache, so it's cleaned up both on
 *  unmount and whenever `documentId` changes to a different file. An open
 *  drawer must never leak the previous document's blob URL. */
export function useDocumentFile(documentId: string | null): {
  objectUrl: string | null;
  mimeType: string | null;
  status: DocumentFileStatus;
} {
  const query = useDocumentFileBlob(documentId);
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

  const status: DocumentFileStatus = query.isError
    ? statusFromError(query.error)
    : blob && objectUrl
      ? 'success'
      : 'loading';

  return { objectUrl, mimeType: blob?.type || null, status };
}
