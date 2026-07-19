import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import { LoginPage } from './login-page';

function renderPage(initialEntry: string | { pathname: string; state?: unknown } = '/login') {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
    >
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>home page</div>} />
          <Route path="/documents" element={<div>documents page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

function ssoStatusResponse(enabled: boolean): Response {
  return new Response(JSON.stringify({ enabled }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

test('successful login stores the access token', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(false);
      return new Response(JSON.stringify({ access_token: 'tok-9' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'pw123456');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  await vi.waitFor(() => expect(getAccessToken()).toBe('tok-9'));
});

test('shows problem+json detail on 401', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(false);
      return new Response(
        JSON.stringify({ title: 'Authentication failed', detail: 'invalid credentials', status: 401 }),
        { status: 401, headers: { 'content-type': 'application/problem+json' } },
      );
    }),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'wrong');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid credentials');
});

test('shows the "Continue with SSO" button when SSO is enabled', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(true);
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  renderPage();
  expect(await screen.findByRole('button', { name: 'Continue with SSO' })).toBeInTheDocument();
});

test('hides the "Continue with SSO" button when SSO is disabled', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(false);
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  renderPage();
  await screen.findByLabelText('Email');
  expect(screen.queryByRole('button', { name: 'Continue with SSO' })).not.toBeInTheDocument();
});

test('successful login navigates to the safe return-to path from location.state', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(false);
      return new Response(JSON.stringify({ access_token: 'tok-9' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  const user = userEvent.setup();
  renderPage({ pathname: '/login', state: { from: '/documents' } });
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'pw123456');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByText('documents page')).toBeInTheDocument();
});

test('successful login falls back to home when location.state.from is not a path (open-redirect guard)', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(false);
      return new Response(JSON.stringify({ access_token: 'tok-9' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  const user = userEvent.setup();
  renderPage({ pathname: '/login', state: { from: 'https://evil.example.com' } });
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'pw123456');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByText('home page')).toBeInTheDocument();
});

test('shows a dismissible SSO error banner when the route has ?sso_error=1', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(true);
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  const user = userEvent.setup();
  renderPage('/login?sso_error=1');
  expect(await screen.findByText(/Single sign-on failed/)).toBeInTheDocument();
  // The SSO button must stay enabled alongside the banner.
  expect(await screen.findByRole('button', { name: 'Continue with SSO' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(screen.queryByText(/Single sign-on failed/)).not.toBeInTheDocument();
});

test('does not show the SSO error banner without the query param', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) return ssoStatusResponse(false);
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  renderPage();
  await screen.findByLabelText('Email');
  expect(screen.queryByText(/Single sign-on failed/)).not.toBeInTheDocument();
});

test('hides the "Continue with SSO" button when the status request errors', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      if (req.url.includes('/api/v1/auth/oidc/status')) {
        return new Response('{}', { status: 500, headers: { 'content-type': 'application/json' } });
      }
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  renderPage();
  await screen.findByLabelText('Email');
  expect(screen.queryByRole('button', { name: 'Continue with SSO' })).not.toBeInTheDocument();
});

test('safeReturnTo rejects protocol-relative and backslash host-escape targets', async () => {
  const { safeReturnTo } = await import('./login-page');
  expect(safeReturnTo('/documents?x=1')).toBe('/documents?x=1');
  expect(safeReturnTo('https://evil.example.com')).toBe('/');
  expect(safeReturnTo('//evil.com')).toBe('/');
  expect(safeReturnTo('/\\evil.com')).toBe('/');
  expect(safeReturnTo('\\\\evil.com')).toBe('/');
  expect(safeReturnTo(42)).toBe('/');
});
