import { X } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useBootstrapStatus, useLogin, useRegister, useSsoStatus } from './mutations';

// Open-redirect guard: only ever navigate to a same-origin path. Rejects
// absolute URLs, protocol-relative ("//evil.com") targets, and their
// backslash variants ("/\evil.com") -- WHATWG URL parsing treats a leading
// "/\" the same as "//", resolving to another host.
export function safeReturnTo(target: unknown): string {
  if (typeof target !== 'string' || !target.startsWith('/')) return '/';
  return target[1] === '/' || target[1] === '\\' ? '/' : target;
}

export function LoginPage() {
  const bootstrap = useBootstrapStatus();
  // Fresh Ragz install with no superadmin yet: offer the one-time
  // "create the first admin" form instead of sign-in. Any other state (the
  // check is loading, errored, or resolved to false) shows normal login.
  if (bootstrap.data?.needs_setup) return <FirstRunSetup />;
  return <SignInForm />;
}

function useLandInApp() {
  const navigate = useNavigate();
  const location = useLocation();
  return () => {
    const from = (location.state as { from?: string } | null)?.from;
    navigate(safeReturnTo(from), { replace: true });
  };
}

function SignInForm() {
  const [searchParams] = useSearchParams();
  const [ssoErrorDismissed, setSsoErrorDismissed] = useState(false);
  const showSsoError = searchParams.get('sso_error') === '1' && !ssoErrorDismissed;
  const login = useLogin();
  const sso = useSsoStatus();
  const landInApp = useLandInApp();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password }, { onSuccess: landInApp });
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
          <div className="mb-1 flex items-center justify-between">
            <Label htmlFor="password" className="mb-0">
              Password
            </Label>
            <Link to="/forgot-password" className="text-[12px] text-accent hover:underline">
              Forgot password?
            </Link>
          </div>
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

function FirstRunSetup() {
  const register = useRegister();
  const landInApp = useLandInApp();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [clientError, setClientError] = useState<string | null>(null);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (password.length < 12) {
      setClientError('Password must be at least 12 characters.');
      return;
    }
    if (password !== confirm) {
      setClientError('Passwords do not match.');
      return;
    }
    setClientError(null);
    // Auto-login on success (register mints the token + refresh cookie), then
    // land in the app -- same post-login flow as sign-in.
    register.mutate({ email, password }, { onSuccess: landInApp });
  };

  const error = clientError ?? (register.isError ? register.error.message : null);

  return (
    <AuthCard title="Create the first admin account">
      <p className="mb-4 text-[12px] text-secondary">
        This is a fresh Ragz install — the first account becomes the superadmin.
      </p>
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
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="confirm">Confirm password</Label>
          <Input
            id="confirm"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
        {error ? (
          <p role="alert" className="text-[12px] text-danger">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" className="w-full" disabled={register.isPending}>
          Create superadmin
        </Button>
      </form>
    </AuthCard>
  );
}
