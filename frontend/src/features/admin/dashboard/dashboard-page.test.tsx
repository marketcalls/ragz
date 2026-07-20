import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import type { DashboardSummaryOut } from '@/api/types';

const useUsageSummary = vi.fn();
vi.mock('./queries', () => ({ useUsageSummary: (days: number) => useUsageSummary(days) }));

import { DashboardPage } from './dashboard-page';

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

const summary: DashboardSummaryOut = {
  by_day: [],
  by_model: [],
  by_user: [{ user_id: 'u1', email: 'alice@example.com', tokens: 456_000, queries: 42 }],
  kpis: { queries: 1234, total_tokens: 567_890, active_users: 12, no_answer_count: 3 },
  queries_per_day: [{ day: '2026-07-01', count: 10 }],
  tokens_by_model_per_day: [{ day: '2026-07-01', model_name: 'gpt-4o', tokens: 500 }],
  answer_quality: {
    audited_count: 0, avg_grounding_score: null, avg_completeness_score: null,
    low_score_count: 0,
  },
  worst_answers: [],
  eval_trend: [],
};

beforeEach(() => {
  useUsageSummary.mockReturnValue({ data: summary, isPending: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders all four KPI tiles and the top-user email', () => {
  renderDashboard();
  expect(screen.getByText('1,234')).toBeInTheDocument();
  expect(screen.getByText('567,890')).toBeInTheDocument();
  expect(screen.getByText('12')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
});

test('defaults to the 30-day range', () => {
  renderDashboard();
  expect(useUsageSummary).toHaveBeenCalledWith(30);
});

test('range switcher fires a refetch with days=7', async () => {
  const user = userEvent.setup();
  renderDashboard();
  await user.click(screen.getByRole('button', { name: '7d' }));
  expect(useUsageSummary).toHaveBeenLastCalledWith(7);
});

test('answer quality tile renders the rounded grounding-score percentage', () => {
  useUsageSummary.mockReturnValue({
    data: {
      ...summary,
      answer_quality: {
        audited_count: 8, avg_grounding_score: 0.72, avg_completeness_score: 0.8,
        low_score_count: 1,
      },
    },
    isPending: false,
  });
  renderDashboard();
  expect(screen.getByText('72%')).toBeInTheDocument();
  expect(screen.getByText('8 audited')).toBeInTheDocument();
});

test('answer quality tile shows a placeholder when nothing has been audited yet', () => {
  renderDashboard();
  expect(screen.getByText('—')).toBeInTheDocument();
  expect(screen.getByText('0 audited')).toBeInTheDocument();
});

test("worst-answers table renders a link to the answer's chat", () => {
  const chatId = 'c1';
  useUsageSummary.mockReturnValue({
    data: {
      ...summary,
      worst_answers: [
        {
          message_id: 'm1', chat_id: chatId, content_snippet: 'a poorly grounded answer',
          grounding_score: 0.1, completeness_score: 0.2, created_at: '2026-07-01T00:00:00Z',
        },
      ],
    },
    isPending: false,
  });
  renderDashboard();
  const link = screen.getByRole('link', { name: /a poorly grounded answer/ });
  expect(link).toHaveAttribute('href', `/chat/${chatId}`);
  expect(screen.getByText('0.10')).toBeInTheDocument();
  expect(screen.getByText('0.20')).toBeInTheDocument();
});

test('empty worst_answers renders neither the table nor a broken empty-state', () => {
  renderDashboard();
  expect(screen.queryByText('Lowest-scoring answers')).not.toBeInTheDocument();
});

test('eval trend table renders workspace name and formatted metrics', () => {
  useUsageSummary.mockReturnValue({
    data: {
      ...summary,
      eval_trend: [
        {
          workspace_id: 'w1',
          workspace_name: 'Engineering',
          hit_rate: 0.8,
          citation_precision: 0.6,
          avg_faithfulness: 4.2,
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
    },
    isPending: false,
  });
  renderDashboard();
  expect(screen.getByText('Eval trend (latest run per workspace)')).toBeInTheDocument();
  expect(screen.getByText('Engineering')).toBeInTheDocument();
  expect(screen.getByText('80%')).toBeInTheDocument();
  expect(screen.getByText('60%')).toBeInTheDocument();
  expect(screen.getByText('4.2')).toBeInTheDocument();
});

test('empty eval_trend renders neither the table nor a broken empty-state', () => {
  renderDashboard();
  expect(screen.queryByText('Eval trend (latest run per workspace)')).not.toBeInTheDocument();
});

test('shows an error message and retry button when the query fails', async () => {
  const refetch = vi.fn();
  useUsageSummary.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load usage summary'),
    refetch,
  });
  renderDashboard();
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
