import { refreshAccessToken } from '@/api/client';
import { getAccessToken } from '@/lib/auth-store';

interface AttemptResult {
  status: number;
  responseText: string;
}

// Backend accepts exactly one multipart part named "file" per POST
// (Body_upload_document_api_v1_workspaces__workspace_id__documents_post —
// schema.d.ts) and returns a single DocumentOut. There is no batch endpoint.
// `folder_id` is an optional extra multipart field: omitted entirely (not
// sent as an empty string) when the file belongs at workspace root.
function attempt(
  workspaceId: string,
  file: File,
  folderId: string | null,
  onLoaded: (loaded: number) => void,
): Promise<AttemptResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/v1/workspaces/${workspaceId}/documents`);
    const token = getAccessToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onLoaded(e.loaded);
    };
    xhr.onload = () => resolve({ status: xhr.status, responseText: xhr.responseText });
    xhr.onerror = () => reject(new Error('network error during upload'));
    const form = new FormData();
    form.append('file', file);
    if (folderId) form.append('folder_id', folderId);
    xhr.send(form);
  });
}

// problem+json `detail` is normally a string, but FastAPI validation errors
// (422) send an array of ValidationError objects — fall back to a generic
// message rather than rendering "[object Object]" in a toast.
function extractDetail(responseText: string): string {
  try {
    const problem = JSON.parse(responseText) as { detail?: unknown };
    if (typeof problem.detail === 'string') return problem.detail;
  } catch {
    /* not JSON — fall through to generic message */
  }
  return 'upload failed';
}

async function uploadOne(
  workspaceId: string,
  file: File,
  folderId: string | null,
  onLoaded: (loaded: number) => void,
): Promise<void> {
  let result = await attempt(workspaceId, file, folderId, onLoaded);
  if (result.status === 401) {
    if (!(await refreshAccessToken())) throw new Error('session expired');
    result = await attempt(workspaceId, file, folderId, onLoaded);
    if (result.status === 401) throw new Error('session expired');
  }
  if (result.status < 200 || result.status >= 300) {
    throw new Error(extractDetail(result.responseText));
  }
}

export interface UploadFailure {
  file: File;
  message: string;
}

export interface UploadItem {
  file: File;
  folderId: string | null;
}

/**
 * Uploads files sequentially, one POST per file (the backend has no batch
 * upload endpoint). XHR, not fetch (fetch has no upload-progress events).
 * `onProgress` receives an aggregate 0-100 percent, bytes-weighted across the
 * whole batch, so the existing single progress-bar UI keeps working unchanged.
 *
 * A single file's failure (e.g. 409 dedup, 413 oversize) does not abort the
 * batch: it's collected into the returned array so the caller can toast it
 * individually by filename while the remaining files continue uploading.
 *
 * Each item carries its own `folderId` so a whole-folder drop (Dropzone's
 * `onFolderFiles`) can upload every file straight into its resolved
 * destination folder in one batch, alongside plain multi-file drops that all
 * share the currently-selected folder.
 */
export async function uploadDocuments(
  workspaceId: string,
  items: UploadItem[],
  onProgress: (pct: number) => void,
): Promise<UploadFailure[]> {
  const totalBytes = items.reduce((sum, item) => sum + item.file.size, 0) || 1;
  let doneBytes = 0;
  const failures: UploadFailure[] = [];
  for (const item of items) {
    try {
      await uploadOne(workspaceId, item.file, item.folderId, (loaded) => {
        onProgress(Math.round(((doneBytes + loaded) / totalBytes) * 100));
      });
    } catch (err) {
      failures.push({ file: item.file, message: err instanceof Error ? err.message : 'upload failed' });
    }
    doneBytes += item.file.size;
    onProgress(Math.round((doneBytes / totalBytes) * 100));
  }
  return failures;
}
