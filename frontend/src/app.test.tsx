import { render, screen } from '@testing-library/react';

import { App } from './app';

test('renders app shell placeholder', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'RagHub' })).toBeInTheDocument();
});
