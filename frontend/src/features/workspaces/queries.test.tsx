import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { useReembedStatus } from './queries';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// Fix round 2 (task review, bug introduced in `936f603`): `refetchInterval:
// (query) => (query.state.data?.finished_at ? false : 1500)` only stopped
// polling once `finished_at` was truthy. For a workspace that has NEVER run
// a re-embed job, GET /reembed-status returns 404, which the queryFn maps
// to `null` -- so `data?.finished_at` is always `undefined`, and the
// ternary kept returning 1500 forever. This exercises the real hook
// end-to-end (not a re-implementation of the decision logic): a job-less
// workspace must fetch once and then stop, not poll indefinitely with
// nothing to observe.
test('does not keep polling /reembed-status for a workspace that has never run a re-embed job', async () => {
  // Real backend 404s are RFC 9457 problem+json bodies, not empty responses --
  // mirror that shape here (an empty body would make openapi-fetch treat the
  // `error` field as falsy, which is not representative of production).
  const fetchMock = vi.fn(async () => jsonResponse({ detail: 'no re-embed job found' }, 404));
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  const { result } = renderHook(() => useReembedStatus('w1'), { wrapper: Wrapper });

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data).toBeNull();

  const callsAfterInitialFetch = fetchMock.mock.calls.length;
  expect(callsAfterInitialFetch).toBeGreaterThan(0);

  // The buggy version would schedule another fetch ~1.5s later regardless.
  // Wait comfortably past that window and confirm nothing further fired.
  await new Promise((resolve) => setTimeout(resolve, 2000));
  expect(fetchMock.mock.calls.length).toBe(callsAfterInitialFetch);
}, 10000);
