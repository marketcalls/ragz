import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useResetPassword } from './mutations';

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const reset = useResetPassword();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [clientError, setClientError] = useState<string | null>(null);

  // RAGZ-PUB-16 mitigation: the reset token arrives as a query param (the
  // only way an emailed link can carry it) but must not linger in browser
  // history or leak via a Referer header on subsequent navigation -- scrub
  // it from the address bar right after mount. This is a one-time DOM
  // side-effect, not a data fetch, so it is exempt from the
  // no-fetch-in-useEffect rule.
  useEffect(() => {
    if (token) {
      window.history.replaceState({}, '', '/reset-password');
    }
  }, [token]);

  if (!token) {
    return (
      <AuthCard title="Invalid link">
        <p className="text-[13px] text-secondary">
          This password reset link is invalid — it is missing its token. Request a new one.
        </p>
        <Button asChild variant="primary" className="mt-4 w-full">
          <Link to="/forgot-password">Request a new link</Link>
        </Button>
      </AuthCard>
    );
  }

  if (reset.isSuccess) {
    return (
      <AuthCard title="Password reset">
        <p className="text-[13px] text-secondary">Your password has been reset.</p>
        <Button asChild variant="primary" className="mt-4 w-full">
          <Link to="/login">Go to sign in</Link>
        </Button>
      </AuthCard>
    );
  }

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
    reset.mutate({ token, new_password: password });
  };

  // Friendly, generic copy on failure -- never surface the raw backend
  // detail here, since a reset-token failure reason (missing/used/expired)
  // is not meaningfully actionable and this route is unauthenticated.
  const error =
    clientError ??
    (reset.isError ? 'This reset link is invalid or has expired. Request a new one.' : null);

  return (
    <AuthCard title="Reset your password">
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <Label htmlFor="password">New password</Label>
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
        <Button type="submit" variant="primary" className="w-full" disabled={reset.isPending}>
          Reset password
        </Button>
      </form>
      {reset.isError ? (
        <p className="mt-3 text-center text-[12px] text-secondary">
          <Link to="/forgot-password" className="text-accent hover:underline">
            Request a new link
          </Link>
        </p>
      ) : null}
    </AuthCard>
  );
}
