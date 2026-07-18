import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { SidebarChatList } from './sidebar-chat-list';

const CHATS = [
  { id: 'c1', title: 'Quarterly revenue projections', workspace_id: 'w1' },
  { id: 'c2', title: 'Onboarding checklist for new hires', workspace_id: 'w1' },
  { id: 'c3', title: 'New chat', workspace_id: 'w1' },
];

function renderList({ initialPath = '/' }: { initialPath?: string } = {}) {
  const patchCalls: { url: string; body: { title: string } }[] = [];
  const deleteCalls: string[] = [];
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    const method = req.method;
    if (url.includes('/workspaces')) {
      return new Response(
        JSON.stringify([{ id: 'w1', name: 'Finance', embedding_model: 'bge-m3', min_score: 0.35 }]),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    }
    if (method === 'PATCH' && /\/chats\/[^/]+$/.test(url)) {
      const body = (await req.json()) as { title: string };
      patchCalls.push({ url, body });
      return new Response(JSON.stringify({ id: 'c1', title: body.title, workspace_id: 'w1' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (method === 'DELETE' && /\/chats\/[^/]+$/.test(url)) {
      deleteCalls.push(url);
      return new Response(null, { status: 204 });
    }
    if (url.includes('/chats')) {
      return new Response(JSON.stringify(CHATS), {
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
      <MemoryRouter initialEntries={[initialPath]}>
        <WorkspaceProvider>
          <LocationProbe />
          <Routes>
            <Route path="/chat/:chatId" element={<SidebarChatList />} />
            <Route path="*" element={<SidebarChatList />} />
          </Routes>
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchMock, patchCalls, deleteCalls };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

test('typing in the search box filters the chat list', async () => {
  renderList();
  expect(await screen.findByText('Quarterly revenue projections')).toBeInTheDocument();
  expect(screen.getByText('Onboarding checklist for new hires')).toBeInTheDocument();
  expect(screen.getByText('New chat')).toBeInTheDocument();

  const search = screen.getByRole('searchbox', { name: 'Search chats' });
  fireEvent.change(search, { target: { value: 'onboarding' } });

  expect(screen.queryByText('Quarterly revenue projections')).not.toBeInTheDocument();
  expect(screen.getByText('Onboarding checklist for new hires')).toBeInTheDocument();
  expect(screen.queryByText('New chat')).not.toBeInTheDocument();
});

test('clearing the search box restores the full list', async () => {
  renderList();
  await screen.findByText('Quarterly revenue projections');

  const search = screen.getByRole('searchbox', { name: 'Search chats' });
  fireEvent.change(search, { target: { value: 'onboarding' } });
  expect(screen.queryByText('Quarterly revenue projections')).not.toBeInTheDocument();

  fireEvent.change(search, { target: { value: '' } });
  expect(screen.getByText('Quarterly revenue projections')).toBeInTheDocument();
  expect(screen.getByText('Onboarding checklist for new hires')).toBeInTheDocument();
  expect(screen.getByText('New chat')).toBeInTheDocument();
});

test('shows "No chats match" when the filter matches nothing', async () => {
  renderList();
  await screen.findByText('Quarterly revenue projections');

  const search = screen.getByRole('searchbox', { name: 'Search chats' });
  fireEvent.change(search, { target: { value: 'zzz-nothing-matches' } });

  expect(screen.getByText('No chats match')).toBeInTheDocument();
});

test('renaming a chat swaps the row for an input and PATCHes the new title', async () => {
  const { patchCalls } = renderList();
  const user = userEvent.setup();
  await screen.findByText('Quarterly revenue projections');

  await user.click(screen.getByRole('button', { name: 'Rename Quarterly revenue projections' }));
  const input = screen.getByRole('textbox', { name: 'Rename chat' });
  expect(input).toHaveValue('Quarterly revenue projections');
  expect(screen.queryByText('Quarterly revenue projections')).not.toBeInTheDocument();

  await user.clear(input);
  await user.type(input, 'Q3 numbers');
  await user.keyboard('{Enter}');

  await vi.waitFor(() => expect(patchCalls).toHaveLength(1));
  const [call] = patchCalls;
  expect(call).toMatchObject({ body: { title: 'Q3 numbers' } });
  expect(call?.url).toContain('/chats/c1');
});

test('escape cancels the rename without calling PATCH', async () => {
  const { patchCalls } = renderList();
  const user = userEvent.setup();
  await screen.findByText('Quarterly revenue projections');

  await user.click(screen.getByRole('button', { name: 'Rename Quarterly revenue projections' }));
  const input = screen.getByRole('textbox', { name: 'Rename chat' });
  await user.type(input, ' more text');
  await user.keyboard('{Escape}');

  expect(screen.getByText('Quarterly revenue projections')).toBeInTheDocument();
  expect(patchCalls).toHaveLength(0);
});

test('deleting a chat shows a confirm dialog and DELETEs on confirm', async () => {
  const { deleteCalls } = renderList();
  const user = userEvent.setup();
  await screen.findByText('Quarterly revenue projections');

  await user.click(screen.getByRole('button', { name: 'Delete Quarterly revenue projections' }));
  expect(
    screen.getByText(/Delete chat .Quarterly revenue projections.\? This cannot be undone\./),
  ).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Delete' }));

  await vi.waitFor(() => expect(deleteCalls).toHaveLength(1));
  expect(deleteCalls.at(0)).toContain('/chats/c1');
});

test('deleting the currently open chat navigates back to /chat', async () => {
  const { deleteCalls } = renderList({ initialPath: '/chat/c1' });
  const user = userEvent.setup();
  await screen.findByText('Quarterly revenue projections');
  expect(screen.getByTestId('location')).toHaveTextContent('/chat/c1');

  await user.click(screen.getByRole('button', { name: 'Delete Quarterly revenue projections' }));
  await user.click(screen.getByRole('button', { name: 'Delete' }));

  await vi.waitFor(() => expect(deleteCalls).toHaveLength(1));
  await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/chat'));
});
