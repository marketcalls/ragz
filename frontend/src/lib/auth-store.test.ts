import { getAccessToken, setAccessToken, subscribeAuth } from './auth-store';

afterEach(() => setAccessToken(null));

test('stores and clears the token in memory', () => {
  expect(getAccessToken()).toBeNull();
  setAccessToken('tok');
  expect(getAccessToken()).toBe('tok');
  setAccessToken(null);
  expect(getAccessToken()).toBeNull();
});

test('notifies subscribers and supports unsubscribe', () => {
  const seen: (string | null)[] = [];
  const unsub = subscribeAuth(() => seen.push(getAccessToken()));
  setAccessToken('a');
  unsub();
  setAccessToken('b');
  expect(seen).toEqual(['a']);
});
