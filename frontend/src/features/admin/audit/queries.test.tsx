import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { useAuditLog } from './queries';

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test('widens date_to to end-of-day before sending', async () => {
  const fetchMock = vi.fn(async (_req: Request) => jsonResponse({ events: [], next_cursor: null }));
  vi.stubGlobal('fetch', fetchMock);

  renderHook(() => useAuditLog({ date_to: '2026-07-19' }), { wrapper });

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const url = new URL(fetchMock.mock.calls[0]![0].url);
  expect(url.searchParams.get('date_to')).toBe('2026-07-19T23:59:59.999');
});

test('leaves an undefined date_to untouched', async () => {
  const fetchMock = vi.fn(async (_req: Request) => jsonResponse({ events: [], next_cursor: null }));
  vi.stubGlobal('fetch', fetchMock);

  renderHook(() => useAuditLog({}), { wrapper });

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const url = new URL(fetchMock.mock.calls[0]![0].url);
  expect(url.searchParams.has('date_to')).toBe(false);
});

// Regression test for a real-world failure: a dev-proxy (or upstream) 500
// with an empty, non-JSON body. openapi-fetch resolves that as
// `{ error: "", response }` -- `error` is falsy (empty string, not
// `undefined`), so a queryFn that only checks `if (error)` never throws and
// silently returns `undefined` as page data. For a plain useQuery that gets
// "rescued" by React Query's own "query data cannot be undefined" guard, but
// useInfiniteQuery has no equivalent guard: the next `getNextPageParam` call
// dereferences the undefined page and the query is left hung (or the
// component crashes on render), never reaching isError. Confirmed live
// against the actual Vite dev proxy behavior when the backend is down.
test('reaches isError (not hung) when the backend returns a malformed empty error body', async () => {
  const fetchMock = vi.fn(
    async () => new Response('', { status: 500, headers: { 'content-type': 'text/plain' } }),
  );
  vi.stubGlobal('fetch', fetchMock);

  const { result } = renderHook(() => useAuditLog({}), { wrapper });

  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.data).toBeUndefined();
  expect(result.current.error).toBeInstanceOf(Error);
});
