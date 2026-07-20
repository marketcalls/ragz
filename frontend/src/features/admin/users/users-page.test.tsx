import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { UserOut } from '@/api/types';

const useUsers = vi.fn();
const usePatchUser = vi.fn();
const useInvite = vi.fn();
const useUserQuota = vi.fn();
const useSetUserQuota = vi.fn();
vi.mock('./queries', () => ({
  useUsers: () => useUsers(),
  usePatchUser: () => usePatchUser(),
  // InviteDialog is always mounted (closed by default) and calls useInvite
  // unconditionally, mirroring ModelsPage/ModelFormDialog.
  useInvite: () => useInvite(),
  // UserQuotaDialog is always mounted (closed by default, target null) and
  // calls these unconditionally, same pattern as InviteDialog/useInvite.
  useUserQuota: (userId: string, enabled: boolean) => useUserQuota(userId, enabled),
  useSetUserQuota: () => useSetUserQuota(),
}));

const useRoles = vi.fn();
const useAssignCustomRole = vi.fn();
vi.mock('../roles/queries', () => ({
  useRoles: () => useRoles(),
  useAssignCustomRole: () => useAssignCustomRole(),
}));

const useGroups = vi.fn();
const useCreateGroup = vi.fn();
const useDeleteGroup = vi.fn();
const useSetGroupMembership = vi.fn();
vi.mock('../groups/queries', () => ({
  // GroupsDialog is always mounted (closed by default); UserGroupsCell is
  // mounted per non-superadmin row. Both call these unconditionally.
  useGroups: () => useGroups(),
  useCreateGroup: () => useCreateGroup(),
  useDeleteGroup: () => useDeleteGroup(),
  useSetGroupMembership: () => useSetGroupMembership(),
}));

import { UsersPage } from './users-page';

const userA: UserOut = {
  id: 'u1',
  email: 'alice@example.com',
  role: 'user',
  active: true,
  custom_role_id: null,
};

beforeEach(() => {
  useUsers.mockReturnValue({ data: [userA], isPending: false, isError: false });
  usePatchUser.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useInvite.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn(), data: undefined });
  useRoles.mockReturnValue({ data: [], isPending: false });
  useAssignCustomRole.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useGroups.mockReturnValue({ data: [], isPending: false });
  useCreateGroup.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useDeleteGroup.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useSetGroupMembership.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useUserQuota.mockReturnValue({ data: undefined, isPending: false, isError: false });
  useSetUserQuota.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders one row per user', () => {
  render(<UsersPage />);
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
});

test('shows an error message and retry button when the users query fails', async () => {
  const refetch = vi.fn();
  useUsers.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load users'),
    refetch,
  });
  render(<UsersPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});

test('clicking Quota opens the dialog and fetches that user\'s quota', async () => {
  render(<UsersPage />);
  expect(useUserQuota).toHaveBeenCalledWith('', false);

  await userEvent.click(screen.getByRole('button', { name: 'Quota' }));

  expect(await screen.findByText('Quota — alice@example.com')).toBeInTheDocument();
  expect(useUserQuota).toHaveBeenLastCalledWith('u1', true);
});

test('shows usage/allocation/reset date and the current override, and saves an edit', async () => {
  const mutate = vi.fn();
  useUserQuota.mockReturnValue({
    data: {
      user_id: 'u1',
      monthly_tokens: 5_000,
      used_tokens: 900,
      allocated_tokens: 5_000,
      resets_at: '2026-08-01T00:00:00Z',
    },
    isPending: false,
    isError: false,
  });
  useSetUserQuota.mockReturnValue({ mutate, isPending: false, isError: false });

  render(<UsersPage />);
  await userEvent.click(screen.getByRole('button', { name: 'Quota' }));

  expect(await screen.findByText(/900/)).toBeInTheDocument();
  const input = screen.getByLabelText('Monthly token override');
  expect(input).toHaveValue(5_000);

  await userEvent.clear(input);
  await userEvent.type(input, '9000');
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));

  expect(mutate).toHaveBeenCalledWith(
    { userId: 'u1', monthlyTokens: 9_000 },
    expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
  );
});

test('clearing the override input saves null (falls back to org default)', async () => {
  const mutate = vi.fn();
  useUserQuota.mockReturnValue({
    data: {
      user_id: 'u1',
      monthly_tokens: 5_000,
      used_tokens: 900,
      allocated_tokens: 5_000,
      resets_at: '2026-08-01T00:00:00Z',
    },
    isPending: false,
    isError: false,
  });
  useSetUserQuota.mockReturnValue({ mutate, isPending: false, isError: false });

  render(<UsersPage />);
  await userEvent.click(screen.getByRole('button', { name: 'Quota' }));

  const input = await screen.findByLabelText('Monthly token override');
  await userEvent.clear(input);
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));

  expect(mutate).toHaveBeenCalledWith(
    { userId: 'u1', monthlyTokens: null },
    expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
  );
});
