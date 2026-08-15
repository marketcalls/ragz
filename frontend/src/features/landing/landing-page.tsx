import { Link } from 'react-router-dom';

// Placeholder shell (Task 2 replaces the body with the full marketing page).
// The nav "Sign in" link is asserted by the gate test, so it lives here from
// the start.
export function LandingPage() {
  return (
    <div className="min-h-screen bg-bg text-ink">
      <nav className="flex items-center justify-between px-6 py-4">
        <span className="text-lg font-bold">Ragz</span>
        <Link
          to="/login"
          className="rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground"
        >
          Sign in
        </Link>
      </nav>
      <main className="px-6 py-24 text-center">
        <h1 className="text-5xl font-bold tracking-tight">Ragz</h1>
      </main>
    </div>
  );
}
