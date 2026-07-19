import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { DocumentsPage } from './documents-page';

const WORKSPACE = { id: 'w1', name: 'Finance', embedding_model: 'bge-m3', min_score: 0.35 };

const FIELDS = [
  { id: 'f1', name: 'doc_type', label: 'Document Type', field_type: 'select', options: ['policy', 'manual'], position: 0 },
];

const DOC = {
  id: 'd1',
  filename: 'policy.pdf',
  mime: 'application/pdf',
  size_bytes: 1024,
  status: 'indexed',
  page_count: 3,
  error: null,
  created_at: '2026-07-18T00:00:00Z',
  pinned: false,
  version: 1,
  lineage_id: 'd1',
  is_current: true,
  approved: false,
  supersedes_document_id: null,
  meta: { doc_type: 'policy' },
};

function renderPage() {
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/metadata-fields')) {
      return new Response(JSON.stringify(FIELDS), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (url.includes('/workspaces') && !url.includes('/documents')) {
      return new Response(JSON.stringify([WORKSPACE]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (url.includes('/documents')) {
      return new Response(JSON.stringify([DOC]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <WorkspaceProvider>
          <DocumentsPage />
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('a filter that narrows groups to zero hides the table instead of rendering an empty one', async () => {
  const user = userEvent.setup();
  renderPage();

  expect(await screen.findByText('policy.pdf')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument();

  await user.selectOptions(screen.getByLabelText('Document Type'), 'manual');

  expect(screen.queryByText('policy.pdf')).not.toBeInTheDocument();
  expect(screen.queryByRole('columnheader', { name: 'Name' })).not.toBeInTheDocument();
  expect(screen.getByText('No documents match the current filters.')).toBeInTheDocument();
});
