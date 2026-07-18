import { decodeClaims } from './jwt';

function fakeJwt(payload: object): string {
  const b64 = (o: object) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256' })}.${b64(payload)}.sig`;
}

test('decodes sub, org, role, exp', () => {
  const claims = decodeClaims(fakeJwt({ sub: 'u1', org: 'o1', role: 'admin', exp: 123 }));
  expect(claims).toEqual({ sub: 'u1', org: 'o1', role: 'admin', exp: 123 });
});

test('returns null for garbage', () => {
  expect(decodeClaims('not-a-jwt')).toBeNull();
  expect(decodeClaims('a.%%%.c')).toBeNull();
});
