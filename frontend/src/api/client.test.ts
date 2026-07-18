import { setAccessToken } from '@/lib/auth-store';

import { authFetch, refreshAccessToken, setOnAuthFailure } from './client';

function res(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  setOnAuthFailure(() => {});
});

test('attaches bearer token', async () => {
  setAccessToken('tok-1');
  const fetchMock = vi.fn(async (req: Request) => {
    expect(req.headers.get('authorization')).toBe('Bearer tok-1');
    return res(200);
  });
  vi.stubGlobal('fetch', fetchMock);
  const r = await authFetch(new Request('http://x/api/v1/workspaces'));
  expect(r.status).toBe(200);
});

test('401 → refresh → retry succeeds; concurrent 401s share ONE refresh', async () => {
  setAccessToken('stale');
  let refreshCalls = 0;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = typeof req === 'string' ? req : req.url;
    if (url.includes('/auth/refresh')) {
      refreshCalls += 1;
      await new Promise((r) => setTimeout(r, 10)); // widen the race window
      return res(200, { access_token: 'fresh' });
    }
    return req.headers.get('authorization') === 'Bearer fresh' ? res(200) : res(401);
  });
  vi.stubGlobal('fetch', fetchMock);

  const [a, b] = await Promise.all([
    authFetch(new Request('http://x/api/v1/workspaces')),
    authFetch(new Request('http://x/api/v1/users')),
  ]);
  expect(a.status).toBe(200);
  expect(b.status).toBe(200);
  expect(refreshCalls).toBe(1);
});

test('refresh failure clears token and fires onAuthFailure', async () => {
  setAccessToken('stale');
  const onFail = vi.fn();
  setOnAuthFailure(onFail);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = typeof req === 'string' ? req : req.url;
      return url.includes('/auth/refresh') ? res(401) : res(401);
    }),
  );
  const r = await authFetch(new Request('http://x/api/v1/workspaces'));
  expect(r.status).toBe(401);
  expect(onFail).toHaveBeenCalledOnce();
  expect(await refreshAccessToken()).toBe(false);
});

test('401 on auth endpoints does NOT trigger refresh', async () => {
  const fetchMock = vi.fn(async () => res(401));
  vi.stubGlobal('fetch', fetchMock);
  await authFetch(new Request('http://x/api/v1/auth/login', { method: 'POST' }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
