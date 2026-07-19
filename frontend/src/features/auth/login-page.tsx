import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useLogin, useSsoStatus } from './mutations';

export function LoginPage() {
  const login = useLogin();
  const sso = useSsoStatus();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password });
  };

  return (
    <AuthCard title="Sign in">
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
