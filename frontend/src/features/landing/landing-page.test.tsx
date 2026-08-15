import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { LandingPage } from './landing-page';

function renderLanding() {
  return render(<MemoryRouter><LandingPage /></MemoryRouter>);
}

describe('LandingPage', () => {
  it('renders the two-tone hero headline', () => {
    renderLanding();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/agentic RAG platform/i);
  });

  it('routes both primary CTAs correctly', () => {
    renderLanding();
    // "Get Started" and the nav "Sign in" both go to /login
    const toLogin = screen.getAllByRole('link', { name: /get started|sign in/i });
    expect(toLogin.length).toBeGreaterThanOrEqual(2);
    toLogin.forEach((a) => expect(a).toHaveAttribute('href', '/login'));
    // GitHub CTA is an external link to the repo
    const gh = screen.getAllByRole('link', { name: /github/i })[0];
    expect(gh).toHaveAttribute('href', expect.stringContaining('github.com/marketcalls/ragz'));
  });

  it('lists all six feature items', () => {
    renderLanding();
    for (const title of [
      /tenant isolation/i,
      /role-based access/i,
      /pluggable document parsing/i,
      /hybrid retrieval/i,
      /API & bot integrations/i,
      /encrypted secrets/i,
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
  });
});
