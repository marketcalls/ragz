import type { DocumentOut } from '@/api/types';

import { groupByLineage } from './versions';

function doc(over: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id: 'd1',
    filename: 'report.pdf',
    mime: 'application/pdf',
    size_bytes: 1024,
    status: 'indexed',
    page_count: 3,
    error: null,
    created_at: '2026-07-18T00:00:00Z',
    pinned: false,
    version: 1,
    lineage_id: 'l1',
    is_current: true,
    approved: false,
    supersedes_document_id: null,
    ...over,
  };
}

test('groups documents by lineage_id into separate groups', () => {
  const v1 = doc({
    id: 'a-v1',
    lineage_id: 'lineage-a',
    version: 1,
    is_current: false,
    created_at: '2026-07-01T00:00:00Z',
  });
  const v2 = doc({
    id: 'a-v2',
    lineage_id: 'lineage-a',
    version: 2,
    is_current: true,
    supersedes_document_id: 'a-v1',
    created_at: '2026-07-10T00:00:00Z',
  });
  const single = doc({
    id: 'b-v1',
    lineage_id: 'lineage-b',
    version: 1,
    is_current: true,
    created_at: '2026-07-05T00:00:00Z',
  });

  const groups = groupByLineage([v1, v2, single]);

  expect(groups).toHaveLength(2);
});

test('current is the is_current row and older versions are sorted by version descending', () => {
  const v1 = doc({ id: 'a-v1', lineage_id: 'lineage-a', version: 1, is_current: false });
  const v3 = doc({ id: 'a-v3', lineage_id: 'lineage-a', version: 3, is_current: false });
  const v2 = doc({ id: 'a-v2', lineage_id: 'lineage-a', version: 2, is_current: true });

  const [group] = groupByLineage([v1, v3, v2]);

  expect(group!.current.id).toBe('a-v2');
  expect(group!.older.map((d) => d.id)).toEqual(['a-v3', 'a-v1']);
});

test('falls back to the highest version when ingest is in flight and no row is is_current yet', () => {
  const v1 = doc({ id: 'a-v1', lineage_id: 'lineage-a', version: 1, is_current: false });
  const v2 = doc({
    id: 'a-v2',
    lineage_id: 'lineage-a',
    version: 2,
    is_current: false,
    status: 'processing',
  });

  const [group] = groupByLineage([v1, v2]);

  expect(group!.current.id).toBe('a-v2');
  expect(group!.older.map((d) => d.id)).toEqual(['a-v1']);
});

test('a single-version lineage has no older versions', () => {
  const single = doc({ id: 'b-v1', lineage_id: 'lineage-b', version: 1, is_current: true });

  const [group] = groupByLineage([single]);

  expect(group!.current.id).toBe('b-v1');
  expect(group!.older).toEqual([]);
});
