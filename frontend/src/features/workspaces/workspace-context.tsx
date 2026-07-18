import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { useWorkspaces } from './queries';

const KEY = 'raghub-workspace';

interface WorkspaceState {
  workspaceId: string | null;
  setWorkspaceId: (id: string) => void;
}

const Ctx = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { data: workspaces } = useWorkspaces();
  const [workspaceId, setWorkspaceIdState] = useState<string | null>(() =>
    localStorage.getItem(KEY),
  );

  // Snap to a real workspace once the list loads (stored id may be stale/foreign).
  useEffect(() => {
    if (!workspaces || workspaces.length === 0) return;
    if (!workspaceId || !workspaces.some((w) => w.id === workspaceId)) {
      setWorkspaceIdState(workspaces[0]?.id ?? null);
    }
  }, [workspaces, workspaceId]);

  const value = useMemo<WorkspaceState>(
    () => ({
      workspaceId,
      setWorkspaceId: (id: string) => {
        localStorage.setItem(KEY, id);
        setWorkspaceIdState(id);
      },
    }),
    [workspaceId],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useWorkspace outside WorkspaceProvider');
  return ctx;
}
