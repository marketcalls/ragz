import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Organization } from './queries';

const useOrganizations = vi.fn();
const useCreateOrganization = vi.fn();
const useUpdateOrganization = vi.fn();
const useDeleteOrganization = vi.fn();
vi.mock('./queries', () => ({
  useOrganizations: () => useOrganizations(),
  // OrganizationsPage always mounts the form dialog (closed by default); it
  // calls useCreateOrganization/useUpdateOrganization unconditionally
  // regardless of open state, mirroring RolesPage/RoleFormDialog. The
  // "Invite admin" InviteDialog (also always mounted, closed by default)
  // resolves to this same mocked module for its org selector.
  useCreateOrganization: () => useCreateOrganization(),
  useUpdateOrganization: () => useUpdateOrganization(),
  useDeleteOrganization: () => useDeleteOrganization(),
}));

// InviteDialog (always mounted, closed by default) calls useInvite
// unconditionally — mirroring useCreateOrganization/useUpdateOrganization above.
const useInvite = vi.fn();
vi.mock('../users/queries', () => ({
  useInvite: () => useInvite(),
}));

// OrganizationsPage is a superadmin-only route; the "Invite admin" shortcut
// only makes sense for that role, so claims are fixed to superadmin here.
vi.mock('@/lib/use-claims', () => ({
  useClaims: () => ({ sub: 'u1', org: 'o1', role: 'superadmin', exp: 9e9 }),
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
  contact_email: 'admin@acme.com',
  industry: 'Technology',
  company_size: '51–200',
  country: 'United States',
};

const orgB: Organization = {
  id: 'o2',
  name: 'Globex',
  sso_domains: null,
  contact_email: null,
  industry: null,
  company_size: null,
  country: null,
};

beforeEach(() => {
  useOrganizations.mockReturnValue({ data: [orgA, orgB], isPending: false, isError: false });
  useCreateOrganization.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn() });
  useUpdateOrganization.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn() });
  useDeleteOrganization.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useInvite.mockReturnValue({ mutate: vi.fn(), isPending: false, reset: vi.fn(), data: undefined });
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

test('"New organization" opens a dialog with the profile fields; submit calls useCreateOrganization with the payload', async () => {
  const mutate = vi.fn();
  useCreateOrganization.mockReturnValue({ mutate, isPending: false, reset: vi.fn() });
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /new organization/i }));
  await user.type(screen.getByLabelText('Name'), 'Initech');
  await user.type(screen.getByLabelText('Contact email'), 'ops@initech.com');
  await user.selectOptions(screen.getByLabelText('Industry'), 'Manufacturing');
  await user.selectOptions(screen.getByLabelText('Company size'), '11–50');
  await user.type(screen.getByLabelText('Country'), 'United States');
  await user.click(screen.getByRole('button', { name: 'Create' }));

  expect(mutate).toHaveBeenCalledWith(
    {
      name: 'Initech',
      contact_email: 'ops@initech.com',
      industry: 'Manufacturing',
      company_size: '11–50',
      country: 'United States',
    },
    expect.anything(),
  );
});

test('editing a row opens a pre-filled dialog seeded from the org; submit calls useUpdateOrganization with id + payload', async () => {
  const mutate = vi.fn();
  useUpdateOrganization.mockReturnValue({ mutate, isPending: false, reset: vi.fn() });
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /edit acme corp/i }));
  const nameInput = screen.getByLabelText('Name');
  expect(nameInput).toHaveValue('Acme Corp');
  expect(screen.getByLabelText('Contact email')).toHaveValue('admin@acme.com');
  expect(screen.getByLabelText('Industry')).toHaveValue('Technology');
  expect(screen.getByLabelText('Company size')).toHaveValue('51–200');
  expect(screen.getByLabelText('Country')).toHaveValue('United States');

  await user.clear(nameInput);
  await user.type(nameInput, 'Acme Corporation');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  expect(mutate).toHaveBeenCalledWith(
    {
      orgId: 'o1',
      name: 'Acme Corporation',
      contact_email: 'admin@acme.com',
      industry: 'Technology',
      company_size: '51–200',
      country: 'United States',
    },
    expect.anything(),
  );
});

test('"Invite admin" opens the invite dialog pre-set to that org + admin role', async () => {
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /invite admin to globex/i }));

  expect(screen.getByText('Invite a user')).toBeInTheDocument();
  expect(screen.getByLabelText('Role')).toHaveValue('admin');
  expect(screen.getByLabelText('Organization')).toHaveValue('o2');
});

test('clicking Delete opens a confirm dialog; confirming calls useDeleteOrganization with the org id', async () => {
  const mutate = vi.fn();
  useDeleteOrganization.mockReturnValue({ mutate, isPending: false });
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /delete acme corp/i }));
  expect(mutate).not.toHaveBeenCalled();

  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText(/acme corp/i)).toBeInTheDocument();

  await user.click(dialog.getByRole('button', { name: /cancel/i }));
  expect(mutate).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: /delete acme corp/i }));
  await user.click(screen.getByRole('button', { name: 'Delete' }));

  expect(mutate).toHaveBeenCalledTimes(1);
  expect(mutate).toHaveBeenCalledWith('o1', expect.anything());
});

test('a rejected delete mutation (e.g. 409 org not empty) surfaces the error via toast', async () => {
  const mutate = vi.fn((_id: string, opts: { onError: (err: Error) => void }) => {
    opts.onError(new Error('organization is not empty — remove its workspaces and users first'));
  });
  useDeleteOrganization.mockReturnValue({ mutate, isPending: false });
  const { toast } = await import('@/components/ui/toaster');
  const user = userEvent.setup();
  render(<OrganizationsPage />);

  await user.click(screen.getByRole('button', { name: /delete acme corp/i }));
  await user.click(screen.getByRole('button', { name: 'Delete' }));

  expect(toast.error).toHaveBeenCalledWith(
    'organization is not empty — remove its workspaces and users first',
  );
});
