import { render, screen } from '@testing-library/react';

import { Button } from './button';

test('primary variant uses inverted primary tokens', () => {
  render(<Button variant="primary">Save</Button>);
  const btn = screen.getByRole('button', { name: 'Save' });
  expect(btn.className).toContain('bg-primary');
  expect(btn.className).toContain('text-primary-foreground');
});

test('defaults to secondary and supports disabled', () => {
  render(<Button disabled>Cancel</Button>);
  const btn = screen.getByRole('button', { name: 'Cancel' });
  expect(btn).toBeDisabled();
  expect(btn.className).toContain('border-line');
});
