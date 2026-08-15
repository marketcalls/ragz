import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import type { components } from '@/api/schema';
import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

mockResponsiveContainerSize();

const useUsageReport = vi.fn();
const exportUsageReport = vi.fn();
vi.mock('./queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./queries')>()),
  useUsageReport: (params: unknown) => useUsageReport(params),
  exportUsageReport: (params: unknown) => exportUsageReport(params),
}));

const useAuthorization = vi.fn();
vi.mock('@/lib/use-authorization', () => ({ useAuthorization: () => useAuthorization() }));

import { ReportsPage } from './reports-page';

type ReportPageOut = components['schemas']['ReportPageOut'];

const report: ReportPageOut = {
  scope: 'self',
  days: 30,
  group_by: 'day',
  rows: [
    {
      group: '2026-08-01',
      prompt_tokens: 1000,
      completion_tokens: 500,
      units: 4,
      cost_usd: 0.25,
      by_feature: {},
    },
    {
      group: '2026-08-02',
      prompt_tokens: 2000,
      completion_tokens: 700,
      units: 6,
      cost_usd: 0.75,
      by_feature: {},
    },
  ],
};

function renderReports() {
  return render(
    <MemoryRouter>
      <ReportsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useUsageReport.mockReturnValue({ data: report, isPending: false });
  useAuthorization.mockReturnValue({
    data: { role: 'user', permissions: new Set(['reports.view.self']), policyVersion: null },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders the chart cards and a table row per group', () => {
  renderReports();
  expect(screen.getByText('Tokens by group')).toBeInTheDocument();
  expect(screen.getByText('Estimated cost by group (USD)')).toBeInTheDocument();
  // Group values appear in both the chart axis (mirrored measurement span) and
  // the table -- scope to the table to assert exactly one data row each.
  const table = screen.getByRole('table');
  expect(within(table).getByText('2026-08-01')).toBeInTheDocument();
  expect(within(table).getByText('2026-08-02')).toBeInTheDocument();
});

test('the totals row sums prompt/completion/units/cost across groups', () => {
  renderReports();
  const table = screen.getByRole('table');
  const totalRow = within(table).getByText('Total').closest('tr') as HTMLElement;
  // 1000 + 2000, 500 + 700, 4 + 6, 0.25 + 0.75
  expect(within(totalRow).getByText('3,000')).toBeInTheDocument();
  expect(within(totalRow).getByText('1,200')).toBeInTheDocument();
  expect(within(totalRow).getByText('10')).toBeInTheDocument();
  expect(within(totalRow).getByText('$1.0000')).toBeInTheDocument();
});

test('shows the empty state when there are no rows', () => {
  useUsageReport.mockReturnValue({ data: { ...report, rows: [] }, isPending: false });
  renderReports();
  expect(screen.getByText('No usage in this range.')).toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

test('defaults a plain member to the 30-day self-scope query', () => {
  renderReports();
  expect(useUsageReport).toHaveBeenLastCalledWith(
    expect.objectContaining({ scope: 'self', days: 30, group_by: 'day' }),
  );
});

test('the Lifetime range switches the query to days=36500', async () => {
  const user = userEvent.setup();
  renderReports();
  await user.click(screen.getByRole('button', { name: 'Lifetime' }));
  expect(useUsageReport).toHaveBeenLastCalledWith(expect.objectContaining({ days: 36500 }));
});

test('the 7d range switches the query to days=7', async () => {
  const user = userEvent.setup();
  renderReports();
  await user.click(screen.getByRole('button', { name: '7d' }));
  expect(useUsageReport).toHaveBeenLastCalledWith(expect.objectContaining({ days: 7 }));
});

test('changing group-by updates the query params', async () => {
  const user = userEvent.setup();
  renderReports();
  await user.selectOptions(screen.getByLabelText('Group by'), 'model');
  expect(useUsageReport).toHaveBeenLastCalledWith(expect.objectContaining({ group_by: 'model' }));
});

test('a plain member sees only the self scope option', () => {
  renderReports();
  const scope = screen.getByLabelText('Scope');
  const options = within(scope).getAllByRole('option');
  expect(options.map((o) => o.getAttribute('value'))).toEqual(['self']);
});

test('a superadmin sees every scope and defaults to platform', () => {
  useAuthorization.mockReturnValue({
    data: { role: 'superadmin', permissions: new Set<string>(), policyVersion: null },
  });
  renderReports();
  const scope = screen.getByLabelText('Scope');
  const options = within(scope).getAllByRole('option');
  expect(options.map((o) => o.getAttribute('value'))).toEqual([
    'self',
    'department',
    'org',
    'platform',
  ]);
  expect(useUsageReport).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'platform' }));
});

test('an admin with org grant defaults to org scope (no platform option)', () => {
  useAuthorization.mockReturnValue({
    data: {
      role: 'admin',
      permissions: new Set(['reports.view.department', 'reports.view.org']),
      policyVersion: null,
    },
  });
  renderReports();
  const scope = screen.getByLabelText('Scope');
  const options = within(scope).getAllByRole('option');
  expect(options.map((o) => o.getAttribute('value'))).toEqual(['self', 'department', 'org']);
  expect(useUsageReport).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'org' }));
});

test('the Export CSV button calls the export endpoint with the current params', async () => {
  const user = userEvent.setup();
  renderReports();
  await user.click(screen.getByRole('button', { name: /export csv/i }));
  expect(exportUsageReport).toHaveBeenCalledWith(
    expect.objectContaining({ scope: 'self', days: 30, group_by: 'day' }),
  );
});
