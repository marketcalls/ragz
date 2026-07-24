import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';

import type { FolderOut } from '@/api/types';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { toast } from '@/components/ui/toaster';

import { FolderCreateDialog, FolderDeleteDialog, FolderRenameDialog } from './folder-dialog';
import type { FolderNode } from './folder-queries';

function folder(over: Partial<FolderOut> & { id: string; name: string }): FolderOut {
  return {
    workspace_id: 'w1',
    parent_folder_id: null,
    created_at: '2026-07-18T00:00:00Z',
    ...over,
  };
}

function node(over: Partial<FolderOut> & { id: string; name: string }): FolderNode {
  return { ...folder(over), children: [] };
}

function renderWithClient(ui: ReactElement, queryClient = new QueryClient()) {
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
  return queryClient;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('creating a folder posts the given parentFolderId and closes on success', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    jsonResponse(
      {
        id: 'new-1',
        workspace_id: 'w1',
        parent_folder_id: 'parent-1',
        name: 'New Folder',
        created_at: '2026-07-24T00:00:00Z',
      },
      201,
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  const onOpenChange = vi.fn();
  const user = userEvent.setup();

  renderWithClient(
    <FolderCreateDialog
      workspaceId="w1"
      parentFolderId="parent-1"
      open
      onOpenChange={onOpenChange}
    />,
  );

  await user.type(screen.getByLabelText('Name'), 'New Folder');
  await user.click(screen.getByRole('button', { name: 'Create' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const req = fetchMock.mock.calls[0]![0] as Request;
  expect(req.method).toBe('POST');
  expect(req.url).toContain('/api/v1/workspaces/w1/folders');
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toEqual({ name: 'New Folder', parent_folder_id: 'parent-1' });
  await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});

test('creating a root folder (parentFolderId null) posts a null parent_folder_id', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    jsonResponse(
      {
        id: 'new-2',
        workspace_id: 'w1',
        parent_folder_id: null,
        name: 'Root',
        created_at: '2026-07-24T00:00:00Z',
      },
      201,
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();

  renderWithClient(
    <FolderCreateDialog workspaceId="w1" parentFolderId={null} open onOpenChange={vi.fn()} />,
  );

  await user.type(screen.getByLabelText('Name'), 'Root');
  await user.click(screen.getByRole('button', { name: 'Create' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const req = fetchMock.mock.calls[0]![0] as Request;
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toEqual({ name: 'Root', parent_folder_id: null });
});

test('rename dialog pre-fills the current folder name and PATCHes the edited value', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    jsonResponse({
      id: 'f1',
      workspace_id: 'w1',
      parent_folder_id: null,
      name: 'New Name',
      created_at: '2026-07-18T00:00:00Z',
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const onOpenChange = vi.fn();
  const user = userEvent.setup();
  const target = node({ id: 'f1', name: 'Old Name' });

  renderWithClient(
    <FolderRenameDialog workspaceId="w1" folder={target} onOpenChange={onOpenChange} />,
  );

  const input = screen.getByLabelText('Name') as HTMLInputElement;
  expect(input.value).toBe('Old Name');

  await user.clear(input);
  await user.type(input, 'New Name');
  await user.click(screen.getByRole('button', { name: 'Save' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const req = fetchMock.mock.calls[0]![0] as Request;
  expect(req.method).toBe('PATCH');
  expect(req.url).toContain('/api/v1/folders/f1');
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toEqual({ name: 'New Name' });
  await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});

test('rename dialog renders closed (no dialog content) when folder is null', () => {
  renderWithClient(<FolderRenameDialog workspaceId="w1" folder={null} onOpenChange={vi.fn()} />);
  expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
});

// A realistic 3-level flat list: root has two children (child-a, child-b);
// child-a has one grandchild. Deleting root must count every descendant --
// not just its direct children -- computed from this FLAT list, not a tree.
const flatFolders: FolderOut[] = [
  folder({ id: 'root', name: 'Root' }),
  folder({ id: 'child-a', name: 'Child A', parent_folder_id: 'root' }),
  folder({ id: 'child-b', name: 'Child B', parent_folder_id: 'root' }),
  folder({ id: 'grandchild', name: 'Grandchild', parent_folder_id: 'child-a' }),
  // An unrelated root-level folder, to make sure it is NOT counted.
  folder({ id: 'unrelated', name: 'Unrelated' }),
];

// Routes fetch by URL substring -- the delete-preview GET and the folder
// DELETE hit different endpoints, and several tests below need both handled
// by the same stubbed `fetch` within a single render.
function routedFetch(
  routes: Array<{ urlIncludes: string; response: () => Response }>,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (req: Request) => {
    const route = routes.find((r) => req.url.includes(r.urlIncludes));
    if (!route) throw new Error(`unhandled request in test: ${req.method} ${req.url}`);
    return route.response();
  });
}

test('before the delete preview loads, the interim text uses the client-side subfolder-only estimate', () => {
  // No fetch stub at all -- the preview request never resolves within this
  // synchronous assertion, so the dialog must show SOMETHING sensible
  // (the pre-existing client-side count, no document mention) rather than
  // blocking on the network call.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => {})),
  );

  renderWithClient(
    <FolderDeleteDialog
      workspaceId="w1"
      folder={node({ id: 'root', name: 'Root' })}
      allFolders={flatFolders}
      onOpenChange={vi.fn()}
    />,
  );

  expect(
    screen.getByText(
      '"Root" and 3 subfolders will be permanently deleted, along with every document inside. This cannot be undone.',
    ),
  ).toBeInTheDocument();
});

test('delete confirmation names the real subfolder AND document counts once the backend preview loads', async () => {
  vi.stubGlobal(
    'fetch',
    routedFetch([
      {
        urlIncludes: '/delete-preview',
        response: () => jsonResponse({ document_count: 5, subfolder_count: 3 }),
      },
    ]),
  );

  renderWithClient(
    <FolderDeleteDialog
      workspaceId="w1"
      folder={node({ id: 'root', name: 'Root' })}
      allFolders={flatFolders}
      onOpenChange={vi.fn()}
    />,
  );

  expect(
    await screen.findByText(
      '"Root" and 3 subfolders will be permanently deleted, along with 5 documents inside. This cannot be undone.',
    ),
  ).toBeInTheDocument();
});

test('delete confirmation uses singular "subfolder"/"document" for exactly one of each', async () => {
  vi.stubGlobal(
    'fetch',
    routedFetch([
      {
        urlIncludes: '/delete-preview',
        response: () => jsonResponse({ document_count: 1, subfolder_count: 1 }),
      },
    ]),
  );

  renderWithClient(
    <FolderDeleteDialog
      workspaceId="w1"
      folder={node({ id: 'child-a', name: 'Child A' })}
      allFolders={flatFolders}
      onOpenChange={vi.fn()}
    />,
  );

  expect(
    await screen.findByText(
      '"Child A" and 1 subfolder will be permanently deleted, along with 1 document inside. This cannot be undone.',
    ),
  ).toBeInTheDocument();
});

test('delete confirmation omits the subfolder clause for a leaf folder but still states its (zero) document count', async () => {
  vi.stubGlobal(
    'fetch',
    routedFetch([
      {
        urlIncludes: '/delete-preview',
        response: () => jsonResponse({ document_count: 0, subfolder_count: 0 }),
      },
    ]),
  );

  renderWithClient(
    <FolderDeleteDialog
      workspaceId="w1"
      folder={node({ id: 'grandchild', name: 'Grandchild' })}
      allFolders={flatFolders}
      onOpenChange={vi.fn()}
    />,
  );

  expect(
    await screen.findByText(
      '"Grandchild" will be permanently deleted, along with 0 documents inside. This cannot be undone.',
    ),
  ).toBeInTheDocument();
});

test('clicking Delete fires the delete mutation and reports the deleted document count', async () => {
  const fetchMock = routedFetch([
    {
      urlIncludes: '/delete-preview',
      response: () => jsonResponse({ document_count: 5, subfolder_count: 3 }),
    },
    { urlIncludes: '/api/v1/folders/root', response: () => jsonResponse({ documents_deleted: 5 }, 202) },
  ]);
  vi.stubGlobal('fetch', fetchMock);
  const onOpenChange = vi.fn();
  const user = userEvent.setup();

  renderWithClient(
    <FolderDeleteDialog
      workspaceId="w1"
      folder={node({ id: 'root', name: 'Root' })}
      allFolders={flatFolders}
      onOpenChange={onOpenChange}
    />,
  );

  // Wait for the preview to settle first so the click below unambiguously
  // targets the folder-delete request in the assertion further down.
  await screen.findByText(
    '"Root" and 3 subfolders will be permanently deleted, along with 5 documents inside. This cannot be undone.',
  );

  await user.click(screen.getByRole('button', { name: 'Delete' }));

  await vi.waitFor(() =>
    expect(toast).toHaveBeenCalledWith('Folder deleted — 5 document(s) removed'),
  );
  await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  const deleteCall = fetchMock.mock.calls.find(
    (c) => (c[0] as Request).method === 'DELETE',
  )?.[0] as Request;
  expect(deleteCall.url).toContain('/api/v1/folders/root');
});

test('delete dialog renders closed (no Delete button) when folder is null', () => {
  renderWithClient(
    <FolderDeleteDialog
      workspaceId="w1"
      folder={null}
      allFolders={flatFolders}
      onOpenChange={vi.fn()}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
});
