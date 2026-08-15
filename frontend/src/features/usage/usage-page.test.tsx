import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DailyUsagePointOut, UsageMeterOut } from '@/api/types';
import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

const useUsageMe = vi.fn();
const useUsageDaily = vi.fn();
vi.mock('./queries', () => ({
  useUsageMe: () => useUsageMe(),
  useUsageDaily: (days: number) => useUsageDaily(days),
}));

import { UsagePage } from './usage-page';

mockResponsiveContainerSize();

const metered: UsageMeterOut = {
  used_tokens: 45_000,
  allocated_tokens: 100_000,
  resets_at: '2026-09-01T00:00:00Z',
  warning: false,
};

const dailySeries: DailyUsagePointOut[] = [
  { date: '2026-08-01', prompt_tokens: 100, completion_tokens: 20 },
  { date: '2026-08-02', prompt_tokens: 0, completion_tokens: 0 },
  { date: '2026-08-03', prompt_tokens: 50, completion_tokens: 10 },
];

beforeEach(() => {
  useUsageDaily.mockReturnValue({ data: dailySeries, isPending: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders the gauge with used/allocated tokens', () => {
  useUsageMe.mockReturnValue({ data: metered, isPending: false });
  render(<UsagePage />);
  expect(screen.getByText('45,000 / 100,000')).toBeInTheDocument();
  expect(screen.getAllByText('45,000').length).toBeGreaterThan(0);
  expect(screen.getAllByText('100,000').length).toBeGreaterThan(0);
});

test('shows the warning banner when warning is true', () => {
  useUsageMe.mockReturnValue({ data: { ...metered, warning: true }, isPending: false });
  render(<UsagePage />);
  expect(screen.getByText(/approaching your token limit/i)).toBeInTheDocument();
});

test('hides the warning banner when warning is false', () => {
  useUsageMe.mockReturnValue({ data: { ...metered, warning: false }, isPending: false });
  render(<UsagePage />);
  expect(screen.queryByText(/approaching your token limit/i)).not.toBeInTheDocument();
});

test('shows an Unlimited state when allocated_tokens is null', () => {
  useUsageMe.mockReturnValue({
    data: { ...metered, allocated_tokens: null },
    isPending: false,
  });
  render(<UsagePage />);
  expect(screen.getByText('Unlimited allocation')).toBeInTheDocument();
  expect(screen.getByText('Unlimited')).toBeInTheDocument();
});

test('shows an Unlimited state when allocated_tokens is 0', () => {
  useUsageMe.mockReturnValue({
    data: { ...metered, allocated_tokens: 0 },
    isPending: false,
  });
  render(<UsagePage />);
  expect(screen.getByText('Unlimited allocation')).toBeInTheDocument();
});

test('shows a loading state', () => {
  useUsageMe.mockReturnValue({ data: undefined, isPending: true });
  render(<UsagePage />);
  expect(screen.getByRole('status')).toBeInTheDocument();
});

test('shows an error message and retry button on failure', async () => {
  const refetch = vi.fn();
  useUsageMe.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load usage'),
    refetch,
  });
  render(<UsagePage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});

test('renders the "Usage over time" chart card with the daily series', () => {
  useUsageMe.mockReturnValue({ data: metered, isPending: false });
  render(<UsagePage />);
  expect(screen.getByText('Usage over time')).toBeInTheDocument();
  // StackedArea renders the prompt/completion legend entries for the series
  expect(screen.getByText('prompt_tokens')).toBeInTheDocument();
  expect(screen.getByText('completion_tokens')).toBeInTheDocument();
});

test('defaults the usage-over-time range to 30 days', () => {
  useUsageMe.mockReturnValue({ data: metered, isPending: false });
  render(<UsagePage />);
  expect(useUsageDaily).toHaveBeenCalledWith(30);
});

test('the range switcher refetches the daily series with days=7', async () => {
  useUsageMe.mockReturnValue({ data: metered, isPending: false });
  render(<UsagePage />);
  await userEvent.click(screen.getByRole('button', { name: '7d' }));
  expect(useUsageDaily).toHaveBeenLastCalledWith(7);
});

test('the daily chart renders its own No data state on an empty series', () => {
  useUsageMe.mockReturnValue({ data: metered, isPending: false });
  useUsageDaily.mockReturnValue({ data: [], isPending: false });
  render(<UsagePage />);
  expect(screen.getByText('Usage over time')).toBeInTheDocument();
  expect(screen.getByText('No data')).toBeInTheDocument();
});
