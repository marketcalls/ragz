import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { UsageMeterOut } from '@/api/types';
import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

const useUsageMe = vi.fn();
vi.mock('./queries', () => ({ useUsageMe: () => useUsageMe() }));

import { UsagePage } from './usage-page';

mockResponsiveContainerSize();

const metered: UsageMeterOut = {
  used_tokens: 45_000,
  allocated_tokens: 100_000,
  resets_at: '2026-09-01T00:00:00Z',
  warning: false,
};

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
