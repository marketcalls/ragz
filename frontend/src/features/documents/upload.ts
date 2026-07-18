import { refreshAccessToken } from '@/api/client';
import { getAccessToken } from '@/lib/auth-store';

function attempt(workspaceId: string, form: FormData, onProgress: (pct: number) => void): Promise<number> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/v1/workspaces/${workspaceId}/documents`);
    const token = getAccessToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.status);
        return;
      }
      if (xhr.status === 401) {
        resolve(401);
        return;
      }
      let detail = `upload failed (${xhr.status})`;
      try {
        const problem = JSON.parse(xhr.responseText) as { detail?: string };
        if (problem.detail) detail = problem.detail;
      } catch {
        /* keep default */
      }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error('network error during upload'));
    xhr.send(form);
  });
}

/** XHR (fetch has no upload progress). Retries once after a token refresh on 401. */
export async function uploadDocuments(
  workspaceId: string,
  files: File[],
  onProgress: (pct: number) => void,
): Promise<void> {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  const status = await attempt(workspaceId, form, onProgress);
  if (status === 401) {
    if (!(await refreshAccessToken())) throw new Error('session expired');
    const retryStatus = await attempt(workspaceId, form, onProgress);
    if (retryStatus === 401) throw new Error('session expired');
  }
}
