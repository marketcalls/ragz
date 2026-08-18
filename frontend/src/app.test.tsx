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

// These two tests dynamically import the WHOLE app (router, fonts, every route
// module) after resetModules, which alone takes ~2.5s locally and longer on a
// cold CI runner. findBy* defaults to 1s and Vitest's per-test cap to 5s, so
// the assertion was racing module evaluation rather than the UI. Scoped to
// these tests rather than raised globally: a flaky gate is worse than no gate,
// but a slack default across the whole suite hides slow-creep regressions.
const SLOW_IMPORT_MS = 15_000;

test('unauthenticated visitors see the public landing at /', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/');
  const { App } = await import('./app');
  render(<App />);
  expect(
    await screen.findByRole('link', { name: /sign in/i }, { timeout: SLOW_IMPORT_MS }),
  ).toBeInTheDocument();
  vi.unstubAllGlobals();
}, SLOW_IMPORT_MS + 5_000);

test('unauthenticated app redirects protected routes to the login page', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/chat');
  const { App } = await import('./app');
  render(<App />);
  expect(
    await screen.findByRole('heading', { name: 'Sign in' }, { timeout: SLOW_IMPORT_MS }),
  ).toBeInTheDocument();
  vi.unstubAllGlobals();
}, SLOW_IMPORT_MS + 5_000);
