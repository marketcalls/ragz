import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { UsageSummaryOut } from '@/api/types';

const useUsageSummary = vi.fn();
vi.mock('./queries', () => ({ useUsageSummary: (days: number) => useUsageSummary(days) }));

import { DashboardPage } from './dashboard-page';

const summary: UsageSummaryOut = {
  by_day: [],
  by_model: [],
  by_user: [{ user_id: 'u1', email: 'alice@example.com', tokens: 456_000, queries: 42 }],
  kpis: { queries: 1234, total_tokens: 567_890, active_users: 12, no_answer_count: 3 },
  queries_per_day: [{ day: '2026-07-01', count: 10 }],
  tokens_by_model_per_day: [{ day: '2026-07-01', model_name: 'gpt-4o', tokens: 500 }],
};

beforeEach(() => {
  useUsageSummary.mockReturnValue({ data: summary, isPending: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders all four KPI tiles and the top-user email', () => {
  render(<DashboardPage />);
  expect(screen.getByText('1,234')).toBeInTheDocument();
  expect(screen.getByText('567,890')).toBeInTheDocument();
  expect(screen.getByText('12')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
});

test('defaults to the 30-day range', () => {
  render(<DashboardPage />);
  expect(useUsageSummary).toHaveBeenCalledWith(30);
});

test('range switcher fires a refetch with days=7', async () => {
  const user = userEvent.setup();
  render(<DashboardPage />);
  await user.click(screen.getByRole('button', { name: '7d' }));
  expect(useUsageSummary).toHaveBeenLastCalledWith(7);
});
