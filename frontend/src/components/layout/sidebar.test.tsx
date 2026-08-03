import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { setAccessToken } from '@/lib/auth-store';
import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { Sidebar } from './sidebar';

const b64 = (o: object) =>
  btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (role: string) =>
  `${b64({ alg: 'HS256' })}.${b64({ sub: 'u1', org: 'o1', role, exp: 9, email: 'a@x.com' })}.s`;

function renderSidebar() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = req.url;
      const body = url.includes('/workspaces')
        ? [{ id: 'w1', name: 'Finance', embedding_model: 'bge-m3', min_score: 0.35 }]
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
          <Sidebar />
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  localStorage.clear();
});

test('user role sees no admin links', async () => {
  setAccessToken(tokenFor('user'));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
  expect(screen.queryByText('Settings')).not.toBeInTheDocument();
});

test('superadmin sees Users, Roles, Models, Settings, and Audit', async () => {
  setAccessToken(tokenFor('superadmin'));
  renderSidebar();
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Roles')).toBeInTheDocument();
  expect(screen.getByText('Models')).toBeInTheDocument();
  expect(screen.getByText('Settings')).toBeInTheDocument();
  expect(screen.getByText('Audit')).toBeInTheDocument();
});

test('admin (non-superadmin) does not see Roles', async () => {
  setAccessToken(tokenFor('admin'));
  renderSidebar();
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Feedback')).toBeInTheDocument();
  expect(screen.queryByText('Roles')).not.toBeInTheDocument();
});
