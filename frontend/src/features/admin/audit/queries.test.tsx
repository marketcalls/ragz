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
