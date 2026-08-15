import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Organization } from './queries';

const useOrganizations = vi.fn();
const useCreateOrganization = vi.fn();
const useRenameOrganization = vi.fn();
vi.mock('./queries', () => ({
  useOrganizations: () => useOrganizations(),
  // OrganizationsPage always mounts the form dialog (closed by default); it
  // calls useCreateOrganization/useRenameOrganization unconditionally
  // regardless of open state, mirroring RolesPage/RoleFormDialog.
  useCreateOrganization: () => useCreateOrganization(),
  useRenameOrganization: () => useRenameOrganization(),
}));

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { OrganizationsPage } from './organizations-page';

const orgA: Organization = {
  id: 'o1',
  name: 'Acme Corp',
  sso_domains: ['acme.com', 'acme.io'],
};

const orgB: Organization = {
  id: 'o2',
  name: 'Globex',
  sso_domains: null,
};

beforeEach(() => {
  useOrganizations.mockReturnValue({ data: [orgA, orgB], isPending: false, isError: false });
  useCreateOrganization.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn() });
  useRenameOrganization.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn() });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders one row per organization with sso domain chips', () => {
  render(<OrganizationsPage />);
  expect(screen.getByText('Acme Corp')).toBeInTheDocument();
  expect(screen.getByText('acme.com')).toBeInTheDocument();
  expect(screen.getByText('acme.io')).toBeInTheDocument();
  expect(screen.getByText('Globex')).toBeInTheDocument();
});

test('shows an error message and retry button when the query fails', async () => {
  const refetch = vi.fn();
  useOrganizations.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load organizations'),
    refetch,
  });
  render(<OrganizationsPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});

test('shows an empty state when there are no organizations', () => {
  useOrganizations.mockReturnValue({ data: [], isPending: false, isError: false });
  render(<OrganizationsPage />);
  expect(screen.getByText(/no organizations yet/i)).toBeInTheDocument();
});

test('"New organization" opens a dialog whose submit calls useCreateOrganization with the typed name', async () => {
  const mutate = vi.fn();
  useCreateOrganization.mockReturnValue({ mutate, isPending: false, reset: vi.fn() });
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /new organization/i }));
  await user.type(screen.getByLabelText('Name'), 'Initech');
  await user.click(screen.getByRole('button', { name: 'Create' }));

  expect(mutate).toHaveBeenCalledWith('Initech', expect.anything());
});

test('renaming a row opens a pre-filled dialog whose submit calls useRenameOrganization with id + new name', async () => {
  const mutate = vi.fn();
  useRenameOrganization.mockReturnValue({ mutate, isPending: false, reset: vi.fn() });
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /rename acme corp/i }));
  const nameInput = screen.getByLabelText('Name');
  expect(nameInput).toHaveValue('Acme Corp');
  await user.clear(nameInput);
  await user.type(nameInput, 'Acme Corporation');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  expect(mutate).toHaveBeenCalledWith({ orgId: 'o1', name: 'Acme Corporation' }, expect.anything());
});
