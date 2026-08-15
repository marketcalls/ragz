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

function renderSidebar({
  role,
  permissions,
  policyVersion = null,
}: {
  role: string;
  permissions: string[];
  policyVersion?: number | null;
}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = req.url;
      if (url.includes('/me/authorization')) {
        return new Response(
          JSON.stringify({ role, permissions, policy_version: policyVersion }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
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

test('a Viewer-permission user sees no admin links', async () => {
  setAccessToken(tokenFor('user'));
  renderSidebar({ role: 'user', permissions: ['search.execute', 'chat.generate'] });
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Audit')).not.toBeInTheDocument();
  expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
  expect(screen.queryByText('Feedback')).not.toBeInTheDocument();
  expect(screen.queryByText('Roles')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
});

test('a user granted only audit.read sees the Audit link but not Users', async () => {
  setAccessToken(tokenFor('user'));
  renderSidebar({ role: 'user', permissions: ['audit.read'] });
  expect(await screen.findByText('Audit')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Roles')).not.toBeInTheDocument();
});

test('superadmin sees every admin link, including platform-only ones', async () => {
  setAccessToken(tokenFor('superadmin'));
  renderSidebar({ role: 'superadmin', permissions: [] });
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Roles')).toBeInTheDocument();
  expect(screen.getByText('Models')).toBeInTheDocument();
  expect(screen.getByText('Settings')).toBeInTheDocument();
  expect(screen.getByText('API Keys')).toBeInTheDocument();
  expect(screen.getByText('Bots')).toBeInTheDocument();
  expect(screen.getByText('Email')).toBeInTheDocument();
  expect(screen.getByText('Audit')).toBeInTheDocument();
  expect(screen.getByText('Health')).toBeInTheDocument();
});

test('a non-superadmin admin with delegated org permissions sees Users/Feedback but not Roles or platform-only links', async () => {
  setAccessToken(tokenFor('admin'));
  renderSidebar({
    role: 'admin',
    permissions: ['users.read', 'feedback.review', 'analytics.view'],
  });
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Feedback')).toBeInTheDocument();
  expect(screen.getByText('Dashboard')).toBeInTheDocument();
  expect(screen.queryByText('Roles')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
  expect(screen.queryByText('API Keys')).not.toBeInTheDocument();
});
