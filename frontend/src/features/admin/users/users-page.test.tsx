import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { UserOut } from '@/api/types';

const useUsers = vi.fn();
const usePatchUser = vi.fn();
const useInvite = vi.fn();
vi.mock('./queries', () => ({
  useUsers: () => useUsers(),
  usePatchUser: () => usePatchUser(),
  // InviteDialog is always mounted (closed by default) and calls useInvite
  // unconditionally, mirroring ModelsPage/ModelFormDialog.
  useInvite: () => useInvite(),
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
