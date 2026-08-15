import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AcceptInvitePage } from './accept-invite-page';

function renderPage(url = '/invite?token=inv-tok') {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <AcceptInvitePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

test('scrubs the token from the URL on mount', () => {
  const spy = vi.spyOn(window.history, 'replaceState');
  renderPage();
  expect(spy).toHaveBeenCalledWith({}, '', '/invite');
});

test('does not scrub when there is no token to begin with', () => {
  const spy = vi.spyOn(window.history, 'replaceState');
  renderPage('/invite');
  expect(spy).not.toHaveBeenCalled();
});

test('rejects passwords under 12 characters client-side', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Password'), 'short');
  await user.type(screen.getByLabelText('Confirm password'), 'short');
  await user.click(screen.getByRole('button', { name: 'Set password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters');
});

test('rejects mismatched confirmation', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText('Password'), 'a-long-password-1');
  await user.type(screen.getByLabelText('Confirm password'), 'a-long-password-2');
  await user.click(screen.getByRole('button', { name: 'Set password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('do not match');
});

test('missing token shows an error state, not a form', () => {
  renderPage('/invite');
  expect(screen.getByText(/invitation link is invalid/i)).toBeInTheDocument();
});
