import type { DocumentOut } from '@/api/types';
import type { StatusTone } from '@/components/ui/status-pill';

export function statusPresentation(doc: Pick<DocumentOut, 'status'>): {
  tone: StatusTone;
  label: string;
} {
  switch (doc.status) {
    case 'indexed':
      return { tone: 'success', label: 'Indexed' };
    case 'failed':
      return { tone: 'danger', label: 'Failed' };
    case 'deleting':
      return { tone: 'muted', label: 'Deleting…' };
    default:
      return { tone: 'accent', label: 'Processing' };
  }
}

export function shouldPoll(docs: readonly DocumentOut[] | undefined): boolean {
  return (docs ?? []).some(
    (d) => d.status === 'queued' || d.status === 'processing' || d.status === 'deleting',
  );
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
