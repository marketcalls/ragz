import { render, screen } from '@testing-library/react';

import { FeedbackPage } from './feedback-page';

const useFeedbackQueue = vi.fn();
vi.mock('./queries', () => ({
  useFeedbackQueue: (filters: unknown) => useFeedbackQueue(filters),
}));

test('renders queue rows with question, answer, and comment', () => {
  useFeedbackQueue.mockReturnValue({
    data: {
      pages: [{
        items: [{
          message_id: 'm1', chat_id: 'c1', workspace_id: 'w1',
          question: 'what is the muster point', answer: 'I could not find it.',
          rating: 'down', comment: 'wrong doc', citations: [],
          created_at: '2026-07-20T00:00:00Z',
        }],
        next_cursor: null,
      }],
    },
    isPending: false,
    isError: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
  });
  render(<FeedbackPage />);
  expect(screen.getByText('what is the muster point')).toBeInTheDocument();
  expect(screen.getByText('wrong doc')).toBeInTheDocument();
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
