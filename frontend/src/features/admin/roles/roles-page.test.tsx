import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { RoleTemplateOut } from '@/api/types';

const useRoles = vi.fn();
const useDeleteRole = vi.fn();
const useCreateRole = vi.fn();
const useUpdateRole = vi.fn();
vi.mock('./queries', () => ({
  useRoles: () => useRoles(),
  useDeleteRole: () => useDeleteRole(),
  // RolesPage always mounts RoleFormDialog (closed by default); it calls
  // useCreateRole/useUpdateRole unconditionally regardless of open state,
  // mirroring ModelsPage/ModelFormDialog.
  useCreateRole: () => useCreateRole(),
  useUpdateRole: () => useUpdateRole(),
}));

import { RolesPage } from './roles-page';

const roleA: RoleTemplateOut = {
  id: 'r1',
  name: 'Reviewer',
  description: 'Reads and comments only',
  permissions: ['documents.upload', 'chat.use'],
};

beforeEach(() => {
  useRoles.mockReturnValue({ data: [roleA], isPending: false, isError: false });
  useDeleteRole.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useCreateRole.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn() });
  useUpdateRole.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn() });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders one row per role template', () => {
  render(<RolesPage />);
  expect(screen.getByText('Reviewer')).toBeInTheDocument();
});

test('shows an error message and retry button when the query fails', async () => {
  const refetch = vi.fn();
  useRoles.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load roles'),
    refetch,
  });
  render(<RolesPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
