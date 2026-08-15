import { render, screen } from '@testing-library/react';

import { FeedbackPage } from './feedback-page';

const useFeedbackQueue = vi.fn();
vi.mock('./queries', () => ({
  useFeedbackQueue: (filters: unknown) => useFeedbackQueue(filters),
}));

const useUsers = vi.fn();
vi.mock('../users/queries', () => ({
  useUsers: () => useUsers(),
}));

const item = (over: Record<string, unknown>) => ({
  message_id: 'm1', chat_id: 'c1', workspace_id: 'w1',
  question: 'what is the muster point', answer: 'I could not find it.',
  rating: 'down', comment: 'wrong doc', citations: [],
  created_at: '2026-07-20T00:00:00Z', user_id: 'u1', user_email: 'a@acme.com',
  ...over,
});

function mockQueue(items: unknown[]) {
  useFeedbackQueue.mockReturnValue({
    data: { pages: [{ items, next_cursor: null }] },
    isPending: false, isError: false, hasNextPage: false,
    fetchNextPage: vi.fn(), isFetchingNextPage: false,
  });
}

beforeEach(() => {
  useFeedbackQueue.mockReset();
  useUsers.mockReset();
  useUsers.mockReturnValue({
    data: [
      { id: 'u1', email: 'a@acme.com' },
      { id: 'u2', email: 'b@acme.com' },
    ],
  });
});

test('defaults to the All rating filter (both up and down show)', () => {
  mockQueue([
    item({ message_id: 'up1', rating: 'up', question: 'praise' }),
    item({ message_id: 'down1', rating: 'down', question: 'complaint' }),
  ]);
  render(<FeedbackPage />);
  // Default rating filter omits `rating` entirely => backend returns all.
  expect(useFeedbackQueue).toHaveBeenCalledWith(
    expect.objectContaining({ rating: undefined }),
  );
  expect(screen.getByText('praise')).toBeInTheDocument();
  expect(screen.getByText('complaint')).toBeInTheDocument();
  expect(screen.getByText('👍')).toBeInTheDocument();
  expect(screen.getByText('👎')).toBeInTheDocument();
});

test('renders the Rating and User columns', () => {
  mockQueue([item({})]);
  render(<FeedbackPage />);
  expect(screen.getByRole('columnheader', { name: 'Rating' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'User' })).toBeInTheDocument();
  // Scope to the table cell: the email also appears as a User-dropdown option.
  expect(screen.getByRole('cell', { name: 'a@acme.com' })).toBeInTheDocument();
});

test('selecting rating, user, and dates updates the query filters', async () => {
  mockQueue([item({})]);
  render(<FeedbackPage />);
  const { default: userEvent } = await import('@testing-library/user-event');
  const user = userEvent.setup();

  await user.selectOptions(screen.getByLabelText('Rating'), 'up');
  await user.selectOptions(screen.getByLabelText('User'), 'u2');
  await user.type(screen.getByLabelText('From'), '2026-01-01');
  await user.type(screen.getByLabelText('To'), '2026-01-31');

  expect(useFeedbackQueue).toHaveBeenLastCalledWith({
    rating: 'up',
    user_id: 'u2',
    start: '2026-01-01T00:00:00',
    end: '2026-01-31T23:59:59.999',
  });
});

test('shows the empty state when no feedback matches', () => {
  mockQueue([]);
  render(<FeedbackPage />);
  expect(screen.getByText('No feedback matches these filters.')).toBeInTheDocument();
});

test('shows the shared error state with a working retry', async () => {
  const refetch = vi.fn();
  useFeedbackQueue.mockReturnValue({
    data: undefined, isPending: false, isError: true,
    error: new Error('boom'), refetch, hasNextPage: false,
    fetchNextPage: vi.fn(), isFetchingNextPage: false,
  });
  render(<FeedbackPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed/i);
  const { default: userEvent } = await import('@testing-library/user-event');
  await userEvent.setup().click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
