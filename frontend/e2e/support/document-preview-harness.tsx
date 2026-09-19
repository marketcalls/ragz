import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';

import { DocumentViewerDrawer } from '@/features/documents/document-viewer-drawer';

export function mountDocumentPreview(filename: string): void {
  const root = document.getElementById('preview-root');
  if (!root) throw new Error('preview-root is missing');
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  createRoot(root).render(
    <QueryClientProvider client={queryClient}>
      <DocumentViewerDrawer
        documentId="00000000-0000-4000-8000-000000000001"
        page={2}
        filename={filename}
        onClose={() => undefined}
      />
    </QueryClientProvider>,
  );
}

declare global {
  interface Window {
    mountDocumentPreview: (filename: string) => void;
    __ragzPreviewExecuted?: boolean;
  }
}
