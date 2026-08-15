import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useAcceptInvite } from './mutations';

export function AcceptInvitePage() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const accept = useAcceptInvite();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [clientError, setClientError] = useState<string | null>(null);

  // RAGZ-PUB-16 mitigation: the invite token arrives as a query param (the
  // only way an emailed link can carry it) but must not linger in browser
  // history or leak via a Referer header on subsequent navigation -- scrub
  // it from the address bar right after mount. This is a one-time DOM
  // side-effect, not a data fetch, so it is exempt from the
  // no-fetch-in-useEffect rule.
  useEffect(() => {
    if (token) {
      window.history.replaceState({}, '', '/invite');
    }
  }, [token]);

  if (!token) {
    return (
      <AuthCard title="Accept invitation">
        <p className="text-[13px] text-secondary">
          This invitation link is invalid — it is missing its token. Ask your admin for a new
          invitation.
        </p>
      </AuthCard>
    );
  }

  if (accept.isSuccess) {
    return (
      <AuthCard title="You're in">
        <p className="text-[13px] text-secondary">Your password is set.</p>
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
    accept.mutate({ token, password });
  };

  const error = clientError ?? (accept.isError ? accept.error.message : null);

  return (
    <AuthCard title="Set your password">
      <form onSubmit={onSubmit} className="space-y-3">
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
        <Button type="submit" variant="primary" className="w-full" disabled={accept.isPending}>
          Set password
        </Button>
      </form>
    </AuthCard>
  );
}
