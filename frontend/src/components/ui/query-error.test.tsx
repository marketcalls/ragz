import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { QueryError } from './query-error';

test('renders the error message as an alert with the shared danger styling', () => {
  render(<QueryError error={new Error('failed to load users')} />);
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('Failed to load: failed to load users');
  expect(alert.className).toContain('text-danger');
});

test('falls back to a generic message for a non-Error thrown value', () => {
  render(<QueryError error="boom" />);
  expect(screen.getByRole('alert')).toHaveTextContent('Failed to load: Something went wrong.');
});

test('omits the retry button when onRetry is not provided', () => {
  render(<QueryError error={new Error('x')} />);
  expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
});

test('retry button calls onRetry when clicked', async () => {
  const onRetry = vi.fn();
  const user = userEvent.setup();
  render(<QueryError error={new Error('x')} onRetry={onRetry} />);
  await user.click(screen.getByRole('button', { name: /retry/i }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});
