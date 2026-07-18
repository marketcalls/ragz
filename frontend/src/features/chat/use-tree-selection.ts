import { useCallback, useMemo, useState } from 'react';

import type { MessageNode } from '@/api/types';

import { selectActivePath, type PathEntry } from './tree';

export function useTreeSelection(roots: readonly MessageNode[] | undefined): {
  path: PathEntry[];
  select: (branchKey: string, id: string) => void;
} {
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const path = useMemo(
    () => selectActivePath(roots ?? [], overrides),
    [roots, overrides],
  );
  const select = useCallback((branchKey: string, id: string) => {
    setOverrides((prev) => ({ ...prev, [branchKey]: id }));
  }, []);
  return { path, select };
}
