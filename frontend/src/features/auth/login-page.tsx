import { X } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useLogin, useSsoStatus } from './mutations';

// Open-redirect guard: only ever navigate to a same-origin path. Rejects
// absolute URLs and protocol-relative ("//evil.com") targets, which start
// with '/' but browsers resolve as scheme-relative to another host.
function safeReturnTo(target: unknown): string {
  return typeof target === 'string' && target.startsWith('/') && !target.startsWith('//') ? target : '/';
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [ssoErrorDismissed, setSsoErrorDismissed] = useState(false);
  const showSsoError = searchParams.get('sso_error') === '1' && !ssoErrorDismissed;
  const login = useLogin();
  const sso = useSsoStatus();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate(
      { email, password },
      {
        onSuccess: () => {
          const from = (location.state as { from?: string } | null)?.from;
          navigate(safeReturnTo(from), { replace: true });
        },
      },
    );
  };

  return (
    <AuthCard title="Sign in">
      {showSsoError ? (
        <div
          role="alert"
          className="mb-3 flex items-start justify-between gap-2 rounded-md bg-warning-soft px-3 py-2 text-[12px] text-warning"
        >
          <span>Single sign-on failed. Try again, or sign in with your password.</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setSsoErrorDismissed(true)}
            className="shrink-0 text-warning hover:opacity-70"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      ) : null}
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {login.isError ? (
          <p role="alert" className="text-[12px] text-danger">
            {login.error.message}
          </p>
        ) : null}
        <Button type="submit" variant="primary" className="w-full" disabled={login.isPending}>
          Sign in
        </Button>
      </form>
      {sso.data?.enabled ? (
        <>
          <div className="my-3 flex items-center gap-2 text-[11px] uppercase text-muted">
            <span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" />
          </div>
          <Button
            type="button"
            className="w-full"
            onClick={() => {
              // full-page navigation: the OIDC dance leaves the SPA and returns
              // with the refresh cookie; the existing session-restore path logs us in
              window.location.href = '/api/v1/auth/oidc/login';
            }}
          >
            Continue with SSO
          </Button>
        </>
      ) : null}
    </AuthCard>
  );
}
