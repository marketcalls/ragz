import { Navigate, Outlet } from 'react-router-dom';

import { useAuthorization } from '@/lib/use-authorization';

export function RequireAction({ action }: { action: string }) {
  const { data, isPending } = useAuthorization();
  if (isPending) return null;
  // UI convenience only -- backend require_action dependencies are the real
  // gate (RBAC-12: backend remains the security boundary).
  const ok = data !== undefined && (data.role === 'superadmin' || data.permissions.has(action));
  return ok ? <Outlet /> : <Navigate to="/chat" replace />;
}
