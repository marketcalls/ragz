import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { LandingGate } from './landing-gate';

vi.mock('@/lib/use-claims', () => ({ useClaims: vi.fn() }));
import { useClaims } from '@/lib/use-claims';

function renderAt() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<LandingGate />} />
        <Route path="/chat" element={<div>chat page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('LandingGate', () => {
  it('renders the landing page for anonymous visitors', () => {
    vi.mocked(useClaims).mockReturnValue(null);
    renderAt();
    expect(screen.getByRole('link', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByText('chat page')).not.toBeInTheDocument();
  });

  it('redirects a logged-in visitor to /chat', () => {
    vi.mocked(useClaims).mockReturnValue({ sub: 'u1', org: 'o1', role: 'user', exp: 9e9 } as never);
    renderAt();
    expect(screen.getByText('chat page')).toBeInTheDocument();
  });
});
