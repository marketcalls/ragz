import { Outlet } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { Sidebar } from './sidebar';

export function AppShell() {
  return (
    <WorkspaceProvider>
      <div className="flex h-screen overflow-hidden bg-bg">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </WorkspaceProvider>
  );
}
