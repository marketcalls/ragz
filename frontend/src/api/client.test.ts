import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import {
  authFetch,
  beginAuthIdentityTransition,
  refreshAccessToken,
  setOnAuthFailure,
} from './client';

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

test('a query string on an auth endpoint does not defeat the pathname match', async () => {
  const fetchMock = vi.fn(async () => res(401));
  vi.stubGlobal('fetch', fetchMock);
  await authFetch(new Request('http://x/api/v1/auth/login?next=1', { method: 'POST' }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test('a non-auth endpoint whose query string happens to contain an auth path still refreshes', async () => {
  setAccessToken('stale');
  const fetchMock = vi.fn(async (req: Request) => {
    const url = typeof req === 'string' ? req : req.url;
    if (url.includes('/auth/refresh')) return res(200, { access_token: 'fresh' });
    return req.headers.get('authorization') === 'Bearer fresh' ? res(200) : res(401);
  });
  vi.stubGlobal('fetch', fetchMock);
  const r = await authFetch(new Request('http://x/api/v1/reports?ref=/api/v1/auth/login'));
  expect(r.status).toBe(200);
});

test('refresh success with a malformed (empty) body is treated as failure and clears the token', async () => {
  setAccessToken('stale');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      const url = typeof req === 'string' ? req : req.url;
      return url.includes('/auth/refresh') ? res(200, {}) : res(401);
    }),
  );
  expect(await refreshAccessToken()).toBe(false);
  expect(getAccessToken()).toBeNull();
});

test('a late refresh cannot overwrite a newer identity or replay its request as that identity', async () => {
  setAccessToken('identity-a');
  let resolveRefresh: ((response: Response) => void) | undefined;
  const refreshResponse = new Promise<Response>((resolve) => {
    resolveRefresh = resolve;
  });
  const protectedRequests: Array<string | null> = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: RequestInfo | URL) => {
      const url = typeof req === 'string' ? req : req instanceof URL ? req.href : req.url;
      if (url.includes('/auth/refresh')) return refreshResponse;
      protectedRequests.push(url);
      return res(401);
    }),
  );
  const pending = authFetch(new Request('http://x/api/v1/workspaces'));
  await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2));
  setAccessToken('identity-b');
  resolveRefresh?.(res(200, { access_token: 'identity-a-refreshed' }));
  expect((await pending).status).toBe(401);
  expect(getAccessToken()).toBe('identity-b');
  expect(protectedRequests).toHaveLength(1);
});

test('a late failed refresh cannot clear a newer login or fire auth failure', async () => {
  setAccessToken('identity-a');
  const onFail = vi.fn();
  setOnAuthFailure(onFail);
  let resolveRefresh: ((response: Response) => void) | undefined;
  const refreshResponse = new Promise<Response>((resolve) => {
    resolveRefresh = resolve;
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: RequestInfo | URL) => {
      const url = typeof req === 'string' ? req : req instanceof URL ? req.href : req.url;
      return url.includes('/auth/refresh') ? refreshResponse : res(401);
    }),
  );
  const pending = authFetch(new Request('http://x/api/v1/workspaces'));
  await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2));
  setAccessToken('identity-b');
  resolveRefresh?.(res(401));
  expect((await pending).status).toBe(401);
  expect(getAccessToken()).toBe('identity-b');
  expect(onFail).not.toHaveBeenCalled();
});

test('a stale request cannot start a cookie-rotating refresh after identity transition', async () => {
  setAccessToken('identity-a');
  let resolveProtected: ((response: Response) => void) | undefined;
  const protectedResponse = new Promise<Response>((resolve) => {
    resolveProtected = resolve;
  });
  const fetchMock = vi.fn(async (req: RequestInfo | URL) => {
    const url = typeof req === 'string' ? req : req instanceof URL ? req.href : req.url;
    if (url.includes('/auth/refresh')) return res(200, { access_token: 'identity-a-refreshed' });
    return protectedResponse;
  });
  vi.stubGlobal('fetch', fetchMock);

  const pending = authFetch(new Request('http://x/api/v1/workspaces'));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
  setAccessToken('identity-b');
  resolveProtected?.(res(401));

  expect((await pending).status).toBe(401);
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(getAccessToken()).toBe('identity-b');
});

test('an explicit identity transition aborts and settles an older refresh first', async () => {
  setAccessToken('identity-a');
  let refreshAborted = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (req: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const url = typeof req === 'string' ? req : req instanceof URL ? req.href : req.url;
          if (!url.includes('/auth/refresh')) return reject(new Error('unexpected URL'));
          init?.signal?.addEventListener('abort', () => {
            refreshAborted = true;
            reject(new DOMException('aborted', 'AbortError'));
          });
        }),
    ),
  );
  const refresh = refreshAccessToken();
  await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledOnce());
  await beginAuthIdentityTransition();
  expect(await refresh).toBe(false);
  expect(refreshAborted).toBe(true);
  expect(getAccessToken()).toBe('identity-a');
});
