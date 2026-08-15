import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ForgotPasswordPage } from './forgot-password-page';

function renderPage() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
    >
      <MemoryRouter initialEntries={['/forgot-password']}>
        <Routes>
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/login" element={<div>login page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test('submitting calls the mutation with the entered email and shows the constant message', async () => {
  let capturedBody: unknown = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      capturedBody = await req.clone().json();
      return new Response(JSON.stringify({ detail: 'accepted' }), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.click(screen.getByRole('button', { name: 'Send reset link' }));
  expect(await screen.findByText(/if that email exists/i)).toBeInTheDocument();
  expect(capturedBody).toEqual({ email: 'a@acme.com' });
});

test('shows the same constant message even when the request errors (no existence leak)', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'boom' }), {
          status: 500,
          headers: { 'content-type': 'application/json' },
        }),
    ),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.click(screen.getByRole('button', { name: 'Send reset link' }));
  expect(await screen.findByText(/if that email exists/i)).toBeInTheDocument();
});

test('the "Back to sign in" link points to /login after submission', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({}), { status: 202, headers: { 'content-type': 'application/json' } }),
    ),
  );
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Email'), 'a@acme.com');
  await user.click(screen.getByRole('button', { name: 'Send reset link' }));
  await screen.findByText(/if that email exists/i);
  expect(screen.getByRole('link', { name: /back to sign in/i })).toHaveAttribute('href', '/login');
});
