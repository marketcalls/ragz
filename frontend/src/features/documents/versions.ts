import type { DocumentOut } from '@/api/types';

export interface LineageGroup {
  current: DocumentOut;
  older: DocumentOut[];
}

export function groupByLineage(docs: DocumentOut[]): LineageGroup[] {
  const byLineage = new Map<string, DocumentOut[]>();
  for (const doc of docs) {
    const list = byLineage.get(doc.lineage_id) ?? [];
    list.push(doc);
    byLineage.set(doc.lineage_id, list);
  }
  const groups: LineageGroup[] = [];
  for (const versions of byLineage.values()) {
    versions.sort((a, b) => b.version - a.version);
    // versions is never empty: every entry in byLineage was seeded by at
    // least one push before being set.
    const current = versions.find((d) => d.is_current) ?? versions[0]!;
    groups.push({ current, older: versions.filter((d) => d !== current) });
  }
  // newest activity first, matching the existing created_at ordering
  groups.sort((a, b) => (a.current.created_at < b.current.created_at ? 1 : -1));
  return groups;
}
