import createClient from 'openapi-fetch';

import {
  getAccessToken,
  getAuthGeneration,
  invalidateAuthGeneration,
  replaceAccessTokenIfCurrent,
  setAccessToken,
} from '@/lib/auth-store';

import type { paths } from './schema';

let onAuthFailure: () => void = () => {};

export function setOnAuthFailure(fn: () => void): void {
  onAuthFailure = fn;
}

// Single-flight: concurrent 401s share one refresh round-trip.
type RefreshResult = 'refreshed' | 'failed-current' | 'stale';
let refreshInFlight: {
  generation: number;
  controller: AbortController;
  promise: Promise<RefreshResult>;
} | null = null;

export function refreshAccessToken(
  expectedGeneration: number = getAuthGeneration(),
): Promise<boolean> {
  return refreshForGeneration(expectedGeneration).then((result) => result === 'refreshed');
}

function refreshForGeneration(generation: number): Promise<RefreshResult> {
  // A response from an old protected request must not start a refresh after
  // logout/login has crossed the identity boundary: Set-Cookie happens before
  // JavaScript can inspect the response, so rejecting it afterward is too late.
  if (getAuthGeneration() !== generation) return Promise.resolve('stale');
  if (refreshInFlight?.generation === generation) return refreshInFlight.promise;
  const controller = new AbortController();
  const slot = { generation, controller, promise: Promise.resolve<RefreshResult>('stale') };
  slot.promise = doRefresh(generation, controller.signal)
    .catch((error: unknown): RefreshResult => {
      if (error instanceof DOMException && error.name === 'AbortError') return 'stale';
      throw error;
    })
    .finally(() => {
      if (refreshInFlight === slot) refreshInFlight = null;
    });
  refreshInFlight = slot;
  return slot.promise;
}

async function doRefresh(generation: number, signal: AbortSignal): Promise<RefreshResult> {
  const res = await fetch('/api/v1/auth/refresh', {
    method: 'POST',
    credentials: 'include',
    signal,
  });
  if (getAuthGeneration() !== generation) return 'stale';
  if (!res.ok) {
    setAccessToken(null);
    return 'failed-current';
  }
  const body: unknown = await res.json().catch(() => null);
  const token =
    body !== null && typeof body === 'object' && 'access_token' in body
      ? (body as { access_token: unknown }).access_token
      : null;
  if (typeof token !== 'string' || token === '') {
    if (getAuthGeneration() !== generation) return 'stale';
    setAccessToken(null);
    return 'failed-current';
  }
  return replaceAccessTokenIfCurrent(token, generation) ? 'refreshed' : 'stale';
}

export async function beginAuthIdentityTransition(options?: {
  clearAccessToken?: boolean;
}): Promise<void> {
  if (options?.clearAccessToken) setAccessToken(null);
  else invalidateAuthGeneration();
  const oldRefresh = refreshInFlight;
  oldRefresh?.controller.abort();
  await oldRefresh?.promise.catch(() => undefined);
}

// Endpoints where a 401 is a real answer, not an expired access token.
const NO_REFRESH = new Set([
  '/api/v1/auth/login',
  '/api/v1/auth/refresh',
  '/api/v1/auth/invitations/accept',
  // Wrong current-password 401 on this authenticated route is a real
  // answer, not an expired access token -- retrying after a refresh would
  // just double the request without changing the outcome.
  '/api/v1/auth/change-password',
]);

export async function authFetch(input: Request): Promise<Response> {
  const requestGeneration = getAuthGeneration();
  const send = (): Promise<Response> => {
    const req = input.clone();
    const token = getAccessToken();
    if (token) req.headers.set('Authorization', `Bearer ${token}`);
    return fetch(req);
  };
  let res = await send();
  if (res.status === 401 && !NO_REFRESH.has(new URL(input.url).pathname)) {
    const refreshResult = await refreshForGeneration(requestGeneration);
    if (refreshResult === 'refreshed' && getAuthGeneration() === requestGeneration) {
      res = await send();
    } else if (refreshResult === 'failed-current') {
      onAuthFailure();
    }
  }
  return res;
}

// Absolute origin, not a bare '/': Node's fetch/Request (used under jsdom in
// tests, and by SSR-adjacent tooling) rejects relative URLs outright — only
// real browsers resolve them against document.baseURI. Same-origin requests
// behave identically either way, so this keeps the Vite dev proxy working.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: 'include',
  fetch: authFetch,
});
