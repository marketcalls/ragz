import { renderHook, act } from '@testing-library/react';

import { setAccessToken } from './auth-store';
import { useClaims } from './use-claims';

const b64 = (o: object) =>
  btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const token = `${b64({ alg: 'HS256' })}.${b64({ sub: 'u1', org: 'o1', role: 'superadmin', exp: 9 })}.s`;

afterEach(() => setAccessToken(null));

test('exposes decoded claims and tracks token changes', () => {
  const { result } = renderHook(() => useClaims());
  expect(result.current).toBeNull();
  act(() => setAccessToken(token));
  expect(result.current?.role).toBe('superadmin');
});
