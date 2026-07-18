import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { InviteDialog } from './invite-dialog';

function renderDialog() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ invite_token: 'raw-tok-123' }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InviteDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('creates an invitation and reveals the one-time link', async () => {
  const user = userEvent.setup();
  renderDialog();
  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.selectOptions(screen.getByLabelText('Role'), 'admin');
  await user.click(screen.getByRole('button', { name: 'Send invite' }));
  const link = await screen.findByText(/\/invite\?token=raw-tok-123/);
  expect(link).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument();
});
