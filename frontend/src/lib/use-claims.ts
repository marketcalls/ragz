import { useSyncExternalStore } from 'react';

import { getAccessToken, subscribeAuth } from './auth-store';
import { decodeClaims, type AccessClaims } from './jwt';

export function useClaims(): AccessClaims | null {
  const token = useSyncExternalStore(subscribeAuth, getAccessToken, getAccessToken);
  return token ? decodeClaims(token) : null;
}
