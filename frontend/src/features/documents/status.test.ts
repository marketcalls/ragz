import type { DocumentOut } from '@/api/types';

import { formatBytes, shouldPoll, statusPresentation } from './status';

test.each([
  ['indexed', 'success', 'Indexed'],
  ['queued', 'accent', 'Processing'],
  ['processing', 'accent', 'Processing'],
  ['failed', 'danger', 'Failed'],
] as const)('%s → %s pill "%s"', (status, tone, label) => {
  expect(statusPresentation({ status })).toEqual({ tone, label });
});

test('shouldPoll only while something is in flight', () => {
  const doc = (status: DocumentOut['status']) => ({ status }) as DocumentOut;
  expect(shouldPoll(undefined)).toBe(false);
  expect(shouldPoll([doc('indexed'), doc('failed')])).toBe(false);
  expect(shouldPoll([doc('indexed'), doc('processing')])).toBe(true);
  expect(shouldPoll([doc('queued')])).toBe(true);
});

test('formatBytes', () => {
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(2048)).toBe('2.0 KB');
  expect(formatBytes(10_485_760)).toBe('10.0 MB');
});
