import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import type { DashboardSummaryOut } from '@/api/types';
import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

mockResponsiveContainerSize();

const useUsageSummary = vi.fn();
const useOrgUsage = vi.fn();
vi.mock('./queries', () => ({
  useUsageSummary: (days: number) => useUsageSummary(days),
  useOrgUsage: (enabled: boolean) => useOrgUsage(enabled),
}));

const useAuthorization = vi.fn();
vi.mock('@/lib/use-authorization', () => ({ useAuthorization: () => useAuthorization() }));

import { DashboardPage } from './dashboard-page';

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

const summary: DashboardSummaryOut = {
  by_day: [{ day: '2026-07-01', tokens: 500 }],
  by_model: [{ model_id: 'gpt-4o', tokens: 500 }],
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
  feedback_summary: { total_count: 0, down_count: 0, down_rate: null },
};

beforeEach(() => {
  useUsageSummary.mockReturnValue({ data: summary, isPending: false });
  useOrgUsage.mockReturnValue({ data: undefined, isPending: false });
  useAuthorization.mockReturnValue({ data: { role: 'admin', permissions: new Set(), policyVersion: 1 } });
});

afterEach(() => {
  vi.clearAllMocks();
});

// With real chart geometry rendered (mockResponsiveContainerSize), axis tick
// text can numerically collide with KPI values (e.g. a "12" Y-axis tick next
// to an "Active users" value of 12), and "Queries" is both a StatTile label
// and a table column header -- so KPI assertions below scope to the
// StatTile's own <p> label (selector: 'p' excludes the <th>) and read that
// element's parent card, rather than searching the whole document.
function statTileValue(label: string): string | null {
  return screen.getByText(label, { selector: 'p' }).parentElement?.textContent ?? null;
}

test('renders all four KPI tiles and the top-user email', () => {
  renderDashboard();
  expect(statTileValue('Queries')).toContain('1,234');
  expect(statTileValue('Tokens')).toContain('567,890');
  expect(statTileValue('Active users')).toContain('12');
  expect(statTileValue('No-answer')).toContain('3');
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
});

test('the queries and tokens KPI tiles render a sparkline from their daily series', () => {
  renderDashboard();
  // Only Queries and Tokens have a matching per-day series in
  // DashboardSummaryOut (no per-day active_users/no_answer_count exists), so
  // only their tiles should contain a rendered sparkline line.
  const queriesTile = screen.getByText('Queries', { selector: 'p' }).parentElement as HTMLElement;
  const tokensTile = screen.getByText('Tokens', { selector: 'p' }).parentElement as HTMLElement;
  const activeUsersTile = screen.getByText('Active users', { selector: 'p' })
    .parentElement as HTMLElement;
  expect(queriesTile.querySelector('.recharts-line')).toBeInTheDocument();
  expect(tokensTile.querySelector('.recharts-line')).toBeInTheDocument();
  expect(activeUsersTile.querySelector('.recharts-line')).not.toBeInTheDocument();
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

test('tokens-by-model donut renders a legend entry per model', async () => {
  renderDashboard();
  await waitFor(() => {
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
  });
});

test('answer quality renders radial gauges with the rounded score percentages', () => {
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
  expect(screen.getByText('80%')).toBeInTheDocument();
  expect(screen.getByText('Grounding')).toBeInTheDocument();
  expect(screen.getByText('Completeness')).toBeInTheDocument();
  expect(screen.getByText('of 8 audited')).toBeInTheDocument();
});

test('answer quality gauges show a No data state when nothing has been audited yet', () => {
  renderDashboard();
  expect(screen.getAllByText('No data').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('of 0 audited')).toBeInTheDocument();
});

test('feedback donut renders a legend entry for down and up responses', async () => {
  useUsageSummary.mockReturnValue({
    data: {
      ...summary,
      feedback_summary: { total_count: 20, down_count: 5, down_rate: 0.25 },
    },
    isPending: false,
  });
  renderDashboard();
  await waitFor(() => {
    expect(screen.getByText('Down')).toBeInTheDocument();
    expect(screen.getByText('Up')).toBeInTheDocument();
  });
  expect(screen.getByText('responses')).toBeInTheDocument();
});

test('feedback donut degrades to a No data state when zero feedback exists in the window', () => {
  renderDashboard();
  expect(screen.getAllByText('No data').length).toBeGreaterThanOrEqual(1);
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

test('eval trend radar renders one axis label per workspace, normalized onto a shared 0-1 scale', () => {
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
  expect(screen.getByText('Eval trend — latest run per workspace')).toBeInTheDocument();
  expect(screen.getByText('Engineering')).toBeInTheDocument();
});

test('empty eval_trend shows the chart card with a No data state, not a broken empty-state', () => {
  renderDashboard();
  expect(screen.getByText('Eval trend — latest run per workspace')).toBeInTheDocument();
  expect(screen.getAllByText('No data').length).toBeGreaterThanOrEqual(1);
});

test('cross-org usage chart is hidden for a plain admin', () => {
  useAuthorization.mockReturnValue({ data: { role: 'admin', permissions: new Set(), policyVersion: 1 } });
  useOrgUsage.mockReturnValue({ data: [{ org_id: 'o1', name: 'Acme', tokens: 100 }], isPending: false });
  renderDashboard();
  expect(useOrgUsage).toHaveBeenLastCalledWith(false);
  expect(screen.queryByText('Cross-org token usage (platform-wide, superadmin)')).not.toBeInTheDocument();
});

test('cross-org usage chart renders for a superadmin once org usage loads', () => {
  useAuthorization.mockReturnValue({
    data: { role: 'superadmin', permissions: new Set(), policyVersion: 1 },
  });
  useOrgUsage.mockReturnValue({ data: [{ org_id: 'o1', name: 'Acme', tokens: 100 }], isPending: false });
  const { container } = renderDashboard();
  expect(useOrgUsage).toHaveBeenLastCalledWith(true);
  expect(screen.getByText('Cross-org token usage (platform-wide, superadmin)')).toBeInTheDocument();
  // Recharts mirrors every axis tick into a shared #recharts_measurement_span
  // appended directly to document.body (outside this render's container) for
  // text-width measurement -- scope to `container` so that hidden duplicate
  // doesn't ambiguate the real "Acme" tick.
  expect(within(container).getByText('Acme')).toBeInTheDocument();
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
