import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ResetPasswordPage } from './reset-password-page';

function renderPage(url = '/reset-password?token=reset-tok') {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
    >
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/login" element={<div>login page</div>} />
          <Route path="/forgot-password" element={<div>forgot page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test('missing token shows an invalid-link state, not a form', () => {
  renderPage('/reset-password');
  expect(screen.getByRole('heading', { name: 'Invalid link' })).toBeInTheDocument();
  expect(screen.queryByLabelText('New password')).not.toBeInTheDocument();
});

test('scrubs the token from the URL on mount', () => {
  const spy = vi.spyOn(window.history, 'replaceState');
  renderPage();
  expect(spy).toHaveBeenCalledWith({}, '', '/reset-password');
});

test('does not scrub when there is no token to begin with', () => {
  const spy = vi.spyOn(window.history, 'replaceState');
  renderPage('/reset-password');
  expect(spy).not.toHaveBeenCalled();
});

test('rejects passwords under 12 characters client-side', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('New password'), 'short');
  await user.type(screen.getByLabelText('Confirm password'), 'short');
  await user.click(screen.getByRole('button', { name: 'Reset password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters');
});

test('rejects mismatched confirmation', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('New password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm password'), 'a-long-password-2');
  await user.click(screen.getByRole('button', { name: 'Reset password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('do not match');
});

test('submits the token and new password, shows a success state on 204', async () => {
  let capturedBody: unknown = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      capturedBody = await req.clone().json();
      return new Response(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('New password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm password'), 'a-long-password-1');
  await user.click(screen.getByRole('button', { name: 'Reset password' }));
  expect(await screen.findByText(/password has been reset/i)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Go to sign in' })).toHaveAttribute('href', '/login');
  expect(capturedBody).toEqual({ token: 'reset-tok', new_password: 'a-long-password-1' });
});

test('shows a friendly error on an invalid/expired token response', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'token expired' }), {
          status: 400,
          headers: { 'content-type': 'application/json' },
        }),
    ),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('New password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm password'), 'a-long-password-1');
  await user.click(screen.getByRole('button', { name: 'Reset password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or has expired/i);
});
