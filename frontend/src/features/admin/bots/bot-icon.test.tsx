import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BotIcon } from './bot-icon';

describe('BotIcon', () => {
  it('renders the bundled icon for a known platform', () => {
    render(<BotIcon platform="telegram" />);
    expect(screen.getByRole('img', { name: /telegram/i })).toBeInTheDocument();
  });
  it('renders a monogram for an unknown platform', () => {
    render(<BotIcon platform="zulip" />);
    expect(screen.getByText('Z')).toBeInTheDocument();
  });
});
