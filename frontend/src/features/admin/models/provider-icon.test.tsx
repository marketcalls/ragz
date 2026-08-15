import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ProviderIcon } from './provider-icon';

describe('ProviderIcon', () => {
  it('renders a monogram (provider initial) when no bundled icon exists', () => {
    render(<ProviderIcon provider={{ id: 'some-unknown-provider', name: 'Zeta Labs', icon: 'some-unknown-provider' }} />);
    expect(screen.getByText('Z')).toBeInTheDocument();
  });
  it('renders an accessible label with the provider name', () => {
    render(<ProviderIcon provider={{ id: 'anthropic', name: 'Anthropic', icon: 'anthropic' }} />);
    expect(screen.getByRole('img', { name: /anthropic/i })).toBeInTheDocument();
  });
  it('gives a provider without a bundled SVG a colored (branded) tile, not a blank one', () => {
    render(<ProviderIcon provider={{ id: 'baseten', name: 'Baseten', icon: 'baseten' }} />);
    const tile = screen.getByRole('img', { name: /baseten/i });
    // Curated brand accent is applied inline (safe colored initial, not a logo).
    expect(tile).toHaveStyle({ backgroundColor: '#6366f1' });
    expect(tile).toHaveTextContent('B');
  });
  it('assigns an uncurated provider a stable (non-empty) fallback color', () => {
    render(<ProviderIcon provider={{ id: 'some-new-co', name: 'Some New Co', icon: 'some-new-co' }} />);
    const tile = screen.getByRole('img', { name: /some new co/i });
    expect(tile.style.backgroundColor).not.toBe('');
  });
});
