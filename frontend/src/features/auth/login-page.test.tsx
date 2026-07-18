import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import { LoginPage } from './login-page';

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('successful login stores the access token', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'tok-9' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
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
    vi.fn(async () =>
      new Response(
        JSON.stringify({ title: 'Authentication failed', detail: 'invalid credentials', status: 401 }),
        { status: 401, headers: { 'content-type': 'application/problem+json' } },
      ),
    ),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.type(screen.getByLabelText('Password'), 'wrong');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid credentials');
});
