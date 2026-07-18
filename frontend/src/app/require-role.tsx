import { Navigate, Outlet } from 'react-router-dom';

import { useClaims } from '@/lib/use-claims';

export function RequireRole({ role }: { role: 'admin' | 'superadmin' }) {
  const claims = useClaims();
  const ok =
    claims !== null &&
    (claims.role === 'superadmin' || (role === 'admin' && claims.role === 'admin'));
  // UI convenience only — the backend role dependencies are the real gate.
  return ok ? <Outlet /> : <Navigate to="/chat" replace />;
}
