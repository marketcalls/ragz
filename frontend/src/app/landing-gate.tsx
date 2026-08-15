import { Navigate } from 'react-router-dom';

import { LandingPage } from '@/features/landing/landing-page';
import { useClaims } from '@/lib/use-claims';

// Public `/`: an anonymous visitor sees the marketing landing; a visitor with
// a live in-memory session goes straight to the app. See the plan's
// "Gate-behavior decision" for why this uses in-memory claims (instant render)
// rather than RequireAuth's async refresh bootstrap.
export function LandingGate() {
  const claims = useClaims();
  return claims ? <Navigate to="/chat" replace /> : <LandingPage />;
}
