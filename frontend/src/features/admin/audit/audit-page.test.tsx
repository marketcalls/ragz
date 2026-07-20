import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const useAuditLog = vi.fn();
vi.mock('./queries', () => ({ useAuditLog: (filters: unknown) => useAuditLog(filters) }));

import { AuditPage } from './audit-page';

const events = [
  {
    id: 'e1',
    created_at: '2026-07-01T00:00:00Z',
    action: 'login',
    actor_id: 'u1',
    org_id: 'org-1',
    target_type: 'session',
    target_id: 's1',
  },
];

afterEach(() => {
  vi.clearAllMocks();
});

test('renders audit rows once loaded', () => {
  useAuditLog.mockReturnValue({
    data: { pages: [{ events, next_cursor: null }] },
    isPending: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
  });
  render(<AuditPage />);
  expect(screen.getByText('login')).toBeInTheDocument();
});

test('shows an error message and retry button when the query fails, with no bare table', async () => {
  const refetch = vi.fn();
  useAuditLog.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load audit log'),
    hasNextPage: false,
    isFetchingNextPage: false,
    refetch,
  });
  render(<AuditPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  // The header-only table must not render on failure (carried finding).
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
