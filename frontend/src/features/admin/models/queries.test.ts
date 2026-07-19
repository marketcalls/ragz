import type { ModelOut } from '@/api/types';

import { adminModelsRefetchInterval } from './queries';

const model = (sync_status: ModelOut['sync_status']) => ({ sync_status }) as ModelOut;

test('adminModelsRefetchInterval polls only while a row is pending', () => {
  expect(adminModelsRefetchInterval(undefined)).toBe(false);
  expect(adminModelsRefetchInterval([])).toBe(false);
  expect(adminModelsRefetchInterval([model('synced'), model('error')])).toBe(false);
  expect(adminModelsRefetchInterval([model('synced'), model('pending')])).toBe(2000);
});
