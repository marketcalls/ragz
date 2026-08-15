import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { MemberOut, UserOut } from '@/api/types';

const useWorkspaceMembers = vi.fn();
const useAddMember = vi.fn();
const useUpdateMemberRole = vi.fn();
const useRemoveMember = vi.fn();
vi.mock('./queries', () => ({
  useWorkspaceMembers: (workspaceId: string) => useWorkspaceMembers(workspaceId),
  useAddMember: (workspaceId: string) => useAddMember(workspaceId),
  useUpdateMemberRole: (workspaceId: string) => useUpdateMemberRole(workspaceId),
  useRemoveMember: (workspaceId: string) => useRemoveMember(workspaceId),
}));

const useUsers = vi.fn();
vi.mock('../admin/users/queries', () => ({
  useUsers: () => useUsers(),
}));

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { MembersSection } from './members-section';

const alice: UserOut = { id: 'u1', email: 'alice@example.com', role: 'user', active: true, custom_role_id: null };
const bob: UserOut = { id: 'u2', email: 'bob@example.com', role: 'user', active: true, custom_role_id: null };
const carol: UserOut = { id: 'u3', email: 'carol@example.com', role: 'user', active: true, custom_role_id: null };

const memberAlice: MemberOut = { user_id: 'u1', role: 'owner' };
const memberBob: MemberOut = { user_id: 'u2', role: 'contributor' };

function setDefaults() {
  useWorkspaceMembers.mockReturnValue({
    data: [memberAlice, memberBob],
    isPending: false,
    isError: false,
  });
  useUsers.mockReturnValue({ data: [alice, bob, carol], isPending: false, isError: false });
  useAddMember.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useUpdateMemberRole.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useRemoveMember.mockReturnValue({ mutate: vi.fn(), isPending: false });
}

beforeEach(() => setDefaults());
afterEach(() => vi.clearAllMocks());

test('renders current members with their email and role', () => {
  render(<MembersSection workspaceId="w1" />);
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
  expect(screen.getByText('bob@example.com')).toBeInTheDocument();
  expect(screen.getByLabelText('Role for alice@example.com')).toHaveValue('owner');
  expect(screen.getByLabelText('Role for bob@example.com')).toHaveValue('contributor');
});

test('changing a member role select calls useUpdateMemberRole().mutate with { userId, role }', async () => {
  const mutate = vi.fn();
  useUpdateMemberRole.mockReturnValue({ mutate, isPending: false });
  const user = userEvent.setup();
  render(<MembersSection workspaceId="w1" />);

  await user.selectOptions(screen.getByLabelText('Role for bob@example.com'), 'manager');

  expect(mutate).toHaveBeenCalledWith(
    { userId: 'u2', role: 'manager' },
    expect.objectContaining({ onError: expect.any(Function) }),
  );
});

test('clicking Remove calls useRemoveMember().mutate with the userId', async () => {
  const mutate = vi.fn();
  useRemoveMember.mockReturnValue({ mutate, isPending: false });
  const user = userEvent.setup();
  render(<MembersSection workspaceId="w1" />);

  const removeButtons = screen.getAllByRole('button', { name: 'Remove' });
  await user.click(removeButtons[0]!);

  expect(mutate).toHaveBeenCalledWith('u1', expect.objectContaining({ onError: expect.any(Function) }));
});

test('the add-member user picker excludes existing members, and Add calls useAddMember().mutate', async () => {
  const mutate = vi.fn();
  useAddMember.mockReturnValue({ mutate, isPending: false });
  const user = userEvent.setup();
  render(<MembersSection workspaceId="w1" />);

  const picker = screen.getByLabelText('Add member');
  expect(screen.queryByRole('option', { name: 'alice@example.com' })).not.toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'bob@example.com' })).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'carol@example.com' })).toBeInTheDocument();

  await user.selectOptions(picker, 'u3');
  await user.selectOptions(screen.getByLabelText('Role for new member'), 'viewer');
  await user.click(screen.getByRole('button', { name: 'Add' }));

  expect(mutate).toHaveBeenCalledWith(
    { user_id: 'u3', role: 'viewer' },
    expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
  );
});
