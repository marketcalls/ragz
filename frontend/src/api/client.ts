import createClient from 'openapi-fetch';

import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import type { paths } from './schema';

let onAuthFailure: () => void = () => {};

export function setOnAuthFailure(fn: () => void): void {
  onAuthFailure = fn;
}

// Single-flight: concurrent 401s share one refresh round-trip.
let refreshInFlight: Promise<boolean> | null = null;

export function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= doRefresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function doRefresh(): Promise<boolean> {
  const res = await fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    setAccessToken(null);
    return false;
  }
  const body: unknown = await res.json().catch(() => null);
  const token =
    body !== null && typeof body === 'object' && 'access_token' in body
      ? (body as { access_token: unknown }).access_token
      : null;
  if (typeof token !== 'string' || token === '') {
    setAccessToken(null);
    return false;
  }
  setAccessToken(token);
  return true;
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
  const send = (): Promise<Response> => {
    const req = input.clone();
    const token = getAccessToken();
    if (token) req.headers.set('Authorization', `Bearer ${token}`);
    return fetch(req);
  };
  let res = await send();
  if (res.status === 401 && !NO_REFRESH.has(new URL(input.url).pathname)) {
    if (await refreshAccessToken()) {
      res = await send();
    } else {
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
