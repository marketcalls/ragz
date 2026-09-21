import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { refreshAccessToken, setOnAuthFailure } from '@/api/client';
import { Spinner } from '@/components/ui/spinner';
import { clearAuthenticatedQueryState } from '@/lib/auth-query-state';
import { getAccessToken } from '@/lib/auth-store';

type Gate = 'checking' | 'authed' | 'anon';

export function RequireAuth() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const location = useLocation();
  const [gate, setGate] = useState<Gate>(() => (getAccessToken() ? 'authed' : 'checking'));

  // Session-restore bootstrap (NOT server state — sanctioned useEffect exception):
  // no in-memory token yet, so try the httpOnly refresh cookie exactly once.
  useEffect(() => {
    if (gate !== 'checking') return;
    let cancelled = false;
    void refreshAccessToken().then(async (ok) => {
      if (cancelled) return;
      if (ok) await clearAuthenticatedQueryState(queryClient);
      if (!cancelled) setGate(ok ? 'authed' : 'anon');
    });
    return () => {
      cancelled = true;
    };
  }, [gate, queryClient]);

  useEffect(() => {
    setOnAuthFailure(() => {
      const from = window.location.pathname + window.location.search;
      void clearAuthenticatedQueryState(queryClient).then(() =>
        navigate('/login', { replace: true, state: { from } }),
      );
    });
    return () => setOnAuthFailure(() => {});
  }, [navigate, queryClient]);

  if (gate === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Signing you in…" />
      </div>
    );
  }
  if (gate === 'anon') {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}
