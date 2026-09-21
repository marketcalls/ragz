// Access token lives in module memory only (never localStorage): XSS cannot read
// what is not persisted, and the httpOnly refresh cookie restores sessions.
let accessToken: string | null = null;
let authGeneration = 0;
const listeners = new Set<() => void>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  authGeneration += 1;
  for (const listener of listeners) listener();
}

export function getAuthGeneration(): number {
  return authGeneration;
}

export function replaceAccessTokenIfCurrent(token: string, generation: number): boolean {
  if (authGeneration !== generation) return false;
  accessToken = token;
  for (const listener of listeners) listener();
  return true;
}

export function invalidateAuthGeneration(): void {
  authGeneration += 1;
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
