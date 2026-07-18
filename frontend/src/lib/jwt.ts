export interface AccessClaims {
  sub: string;
  org: string;
  role: 'superadmin' | 'admin' | 'user';
  exp: number;
}

/** Payload decode only — no signature verification. UI hinting; the server decides. */
export function decodeClaims(token: string): AccessClaims | null {
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    const payload = JSON.parse(json) as Record<string, unknown>;
    if (
      typeof payload.sub !== 'string' ||
      typeof payload.org !== 'string' ||
      typeof payload.role !== 'string' ||
      typeof payload.exp !== 'number'
    ) {
      return null;
    }
    return {
      sub: payload.sub,
      org: payload.org,
      role: payload.role as AccessClaims['role'],
      exp: payload.exp,
    };
  } catch {
    return null;
  }
}
