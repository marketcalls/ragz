import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { SidebarChatList } from './sidebar-chat-list';

function renderList() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = req.url;
      const body = url.includes('/workspaces')
        ? [{ id: 'w1', name: 'Finance', embedding_model: 'bge-m3', min_score: 0.35 }]
        : url.includes('/chats')
          ? [
              { id: 'c1', title: 'Quarterly revenue projections', workspace_id: 'w1' },
              { id: 'c2', title: 'Onboarding checklist for new hires', workspace_id: 'w1' },
              { id: 'c3', title: 'New chat', workspace_id: 'w1' },
            ]
          : [];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <WorkspaceProvider>
          <SidebarChatList />
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
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
