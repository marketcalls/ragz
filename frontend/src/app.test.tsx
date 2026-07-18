import { render, screen } from '@testing-library/react';

import { App } from './app';

test('unauthenticated app redirects to the login page', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/');
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  vi.unstubAllGlobals();
});
