import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { authFetch } from '@/api/client';
import { LoginPage } from '@/features/auth/login-page';
import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import { RequireAuth } from './require-auth';

function renderProtected() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/secret']}>
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route element={<RequireAuth />}>
            <Route path="/secret" element={<div>secret page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('renders children when a token exists', () => {
  setAccessToken('tok');
  renderProtected();
  expect(screen.getByText('secret page')).toBeInTheDocument();
});

test('restores session via refresh cookie when no token', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ access_token: 'restored' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    ),
  );
  renderProtected();
  expect(await screen.findByText('secret page')).toBeInTheDocument();
});

test('redirects to /login when refresh fails', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{}', { status: 401 })),
  );
  renderProtected();
  expect(await screen.findByText('login page')).toBeInTheDocument();
});

test('return-to-url: unauthenticated visit lands back on the original route after login', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = typeof req === 'string' ? req : req.url;
      if (url.includes('/auth/refresh')) return new Response('{}', { status: 401 });
      if (url.includes('/auth/oidc/status')) {
        return new Response(JSON.stringify({ enabled: false }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (url.includes('/auth/login')) {
        return new Response(JSON.stringify({ access_token: 'tok-restored' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/documents']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/documents" element={<div>documents page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByLabelText('Email');
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'pw123456');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByText('documents page')).toBeInTheDocument();
});

test('A expiry then B login never renders cached or late A query data', async () => {
  window.history.pushState({}, '', '/secret');
  setAccessToken('identity-a');
  let resolveLateA: ((value: string) => void) | undefined;
  const lateA = new Promise<string>((resolve) => {
    resolveLateA = resolve;
  });
  const sensitiveQuery = vi.fn(async () => {
    if (getAccessToken() === 'identity-a') return lateA;
    return 'B workspace';
  });

  function SensitivePage() {
    const query = useQuery({ queryKey: ['sensitive-workspace'], queryFn: sensitiveQuery });
    return <div>{query.data ?? 'loading sensitive data'}</div>;
  }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: RequestInfo | URL) => {
      const url = typeof req === 'string' ? req : req instanceof URL ? req.href : req.url;
      if (url.includes('/api/v1/auth/refresh')) return new Response('{}', { status: 401 });
      if (url.includes('/api/v1/auth/bootstrap-status')) {
        return new Response(JSON.stringify({ needs_setup: false }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (url.includes('/api/v1/auth/oidc/status')) {
        return new Response(JSON.stringify({ enabled: false }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (url.includes('/api/v1/auth/login')) {
        return new Response(JSON.stringify({ access_token: 'identity-b' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      return new Response('{}', { status: 401 });
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queryClient.setQueryData(['sensitive-workspace'], 'A cached workspace');
  await queryClient.invalidateQueries({ queryKey: ['sensitive-workspace'] });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/secret']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/secret" element={<SensitivePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText('A cached workspace')).toBeInTheDocument();
  await vi.waitFor(() => expect(sensitiveQuery).toHaveBeenCalled());

  await act(async () => {
    await authFetch(new Request('http://x/api/v1/workspaces'));
  });
  await screen.findByLabelText('Email');
  expect(screen.queryByText('A cached workspace')).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('Email'), 'b@acme.com');
  await user.type(screen.getByLabelText('Password'), 'pw123456');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByText('B workspace')).toBeInTheDocument();

  await act(async () => {
    resolveLateA?.('A late workspace');
    await lateA;
  });
  expect(screen.queryByText(/A (cached|late) workspace/)).not.toBeInTheDocument();
  expect(screen.getByText('B workspace')).toBeInTheDocument();
});
