import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
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

function renderPage({ folders = [] as unknown[] } = {}) {
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/metadata-fields')) {
      return new Response(JSON.stringify(FIELDS), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    // Must be checked before the generic `/workspaces` branch below --
    // `/api/v1/workspaces/{id}/folders` also contains `/workspaces` and
    // would otherwise be misrouted to the workspace-list stub.
    if (url.includes('/folders')) {
      return new Response(JSON.stringify(folders), {
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

test('the folder sidebar renders sensibly for a workspace with zero folders (the default/common case)', async () => {
  renderPage({ folders: [] });

  expect(await screen.findByText('policy.pdf')).toBeInTheDocument();
  const sidebar = within(screen.getByRole('navigation', { name: 'Folders' }));
  // Only the "All documents" row should render -- no bogus/leftover folder rows.
  expect(sidebar.getByRole('button', { name: 'All documents' })).toBeInTheDocument();
  expect(sidebar.getAllByRole('button')).toHaveLength(1);
});

// Regression test for the rename pre-fill bug: FolderRenameDialog is rendered
// UNCONDITIONALLY in DocumentsPage (no key, before this fix), so it mounts
// exactly once -- opening Rename on any folder just updates that same
// instance's `folder` prop, and useState(folder?.name ?? '') never re-runs.
// This must drive the real always-mounted DocumentsPage instance (not a
// fresh isolated FolderRenameDialog mount, which is all folder-dialog.test.tsx
// exercised and is why this bug slipped through that suite).
test('rename dialog resyncs its name field across successive opens on different folders (regression: pre-fill bug)', async () => {
  const user = userEvent.setup();
  renderPage({
    folders: [
      { id: 'a', workspace_id: 'w1', parent_folder_id: null, name: 'Alpha', created_at: '2026-07-18T00:00:00Z' },
      { id: 'b', workspace_id: 'w1', parent_folder_id: null, name: 'Beta', created_at: '2026-07-18T00:00:00Z' },
    ],
  });

  expect(await screen.findByText('policy.pdf')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Rename Alpha' }));
  expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Alpha');

  // Close without saving.
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();

  // Opening Rename on a DIFFERENT folder must show ITS real name -- not
  // "Alpha" left over from the previous open, and not empty.
  await user.click(screen.getByRole('button', { name: 'Rename Beta' }));
  expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Beta');
});
