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
});
