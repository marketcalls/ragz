import { render, screen } from '@testing-library/react';

// `router.tsx` builds its `createBrowserRouter` singleton at import time, and
// that singleton caches its location on push/replace (not on raw
// `window.history.pushState`). Each test below visits a different starting
// URL, so each one resets the module registry and re-imports `App` fresh —
// otherwise the second test would render against the first test's stale
// router location.
beforeEach(() => {
  vi.resetModules();
});

test('unauthenticated visitors see the public landing at /', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/');
  const { App } = await import('./app');
  render(<App />);
  expect(await screen.findByRole('link', { name: /sign in/i })).toBeInTheDocument();
  vi.unstubAllGlobals();
});

test('unauthenticated app redirects protected routes to the login page', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/chat');
  const { App } = await import('./app');
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  vi.unstubAllGlobals();
});
