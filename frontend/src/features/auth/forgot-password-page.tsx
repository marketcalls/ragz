import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useForgotPassword } from './mutations';

// Enumeration-safety (RAGZ-PUB-06): the backend response is already
// enum-safe (identical 202 whether or not the email exists) -- the UI must
// not undo that by branching copy on success vs. error. Once submitted this
// constant message is shown no matter how the mutation settles.
const CONSTANT_MESSAGE = "If that email exists, we've sent a reset link.";

export function ForgotPasswordPage() {
  const forgot = useForgotPassword();
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    forgot.mutate({ email });
  };

  if (submitted) {
    return (
      <AuthCard title="Check your email">
        <p className="text-[13px] text-secondary">{CONSTANT_MESSAGE}</p>
        <Button asChild variant="primary" className="mt-4 w-full">
          <Link to="/login">Back to sign in</Link>
        </Button>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Forgot password">
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
        <Button type="submit" variant="primary" className="w-full" disabled={forgot.isPending}>
          Send reset link
        </Button>
      </form>
      <p className="mt-4 text-center text-[12px] text-secondary">
        <Link to="/login" className="text-accent hover:underline">
          Back to sign in
        </Link>
      </p>
    </AuthCard>
  );
}
