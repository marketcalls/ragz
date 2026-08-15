import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ChangePasswordForm } from './change-password-form';

function renderForm() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
    >
      <ChangePasswordForm />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test('rejects a new password under 12 characters client-side', async () => {
  const user = userEvent.setup();
  renderForm();
  await user.type(screen.getByLabelText('Current password'), 'currentpw123');
  await user.type(screen.getByLabelText('New password'), 'short');
  await user.type(screen.getByLabelText('Confirm new password'), 'short');
  await user.click(screen.getByRole('button', { name: 'Change password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters');
});

test('rejects mismatched confirmation', async () => {
  const user = userEvent.setup();
  renderForm();
  await user.type(screen.getByLabelText('Current password'), 'currentpw123');
  await user.type(screen.getByLabelText('New password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm new password'), 'a-long-password-2');
  await user.click(screen.getByRole('button', { name: 'Change password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('do not match');
});

test('submits current + new password, clears fields, and shows a confirmation on success', async () => {
  let capturedBody: unknown = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      capturedBody = await req.clone().json();
      return new Response(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();
  renderForm();
  await user.type(screen.getByLabelText('Current password'), 'currentpw123');
  await user.type(screen.getByLabelText('New password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm new password'), 'a-long-password-1');
  await user.click(screen.getByRole('button', { name: 'Change password' }));

  expect(await screen.findByText(/you may need to sign in again/i)).toBeInTheDocument();
  expect(capturedBody).toEqual({
    current_password: 'currentpw123',
    new_password: 'a-long-password-1',
  });
  expect(screen.getByLabelText('Current password')).toHaveValue('');
  expect(screen.getByLabelText('New password')).toHaveValue('');
  expect(screen.getByLabelText('Confirm new password')).toHaveValue('');
});

test('shows a field error and keeps values on a 401 (wrong current password)', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'invalid credentials' }), {
          status: 401,
          headers: { 'content-type': 'application/problem+json' },
        }),
    ),
  );
  const user = userEvent.setup();
  renderForm();
  await user.type(screen.getByLabelText('Current password'), 'wrongpassword');
  await user.type(screen.getByLabelText('New password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm new password'), 'a-long-password-1');
  await user.click(screen.getByRole('button', { name: 'Change password' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('invalid credentials');
  expect(screen.getByLabelText('Current password')).toHaveValue('wrongpassword');
});
