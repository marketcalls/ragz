import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { useChangePassword } from '@/features/auth/mutations';

export function ChangePasswordForm() {
  const change = useChangePassword();
  const [currentPassword, setCurrentPassword] = useState('');
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
    change.mutate(
      { current_password: currentPassword, new_password: password },
      {
        // Only clear on success -- a failed attempt (e.g. wrong current
        // password) keeps what was typed instead of silently discarding it.
        onSuccess: () => {
          setCurrentPassword('');
          setPassword('');
          setConfirm('');
        },
      },
    );
  };

  const error = clientError ?? (change.isError ? change.error.message : null);

  return (
    <form onSubmit={onSubmit} className="max-w-sm space-y-3">
      <div>
        <Label htmlFor="current-password">Current password</Label>
        <Input
          id="current-password"
          type="password"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="new-password">New password</Label>
        <Input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="confirm-password">Confirm new password</Label>
        <Input
          id="confirm-password"
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
      {change.isSuccess ? (
        <p className="text-[12px] text-success">
          Password changed. Other sessions were signed out — you may need to sign in again there.
        </p>
      ) : null}
      <Button type="submit" variant="primary" disabled={change.isPending}>
        {change.isPending ? 'Changing…' : 'Change password'}
      </Button>
    </form>
  );
}
