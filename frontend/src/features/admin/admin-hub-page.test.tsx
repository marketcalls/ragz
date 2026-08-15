import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/lib/use-authorization', () => ({ useAuthorization: vi.fn() }));
vi.mock('@/lib/use-claims', () => ({ useClaims: vi.fn() }));

import { useAuthorization } from '@/lib/use-authorization';
import { useClaims } from '@/lib/use-claims';

import { AdminHubPage } from './admin-hub-page';

function renderHub() {
  return render(
    <MemoryRouter>
      <AdminHubPage />
    </MemoryRouter>,
  );
}

function mockAuth({
  role,
  permissions = [],
}: {
  role: 'superadmin' | 'admin' | 'user';
  permissions?: string[];
}) {
  vi.mocked(useAuthorization).mockReturnValue({
    data: { role, permissions: new Set(permissions), policyVersion: 1 },
  } as never);
  vi.mocked(useClaims).mockReturnValue({
    sub: 'u1',
    org: 'o1',
    role,
    exp: 9e9,
    email: 'a@x.com',
  } as never);
}

afterEach(() => {
  vi.clearAllMocks();
});

test('a superadmin sees every admin card', () => {
  mockAuth({ role: 'superadmin' });
  renderHub();
  for (const label of [
    'Dashboard',
    'Users',
    'Feedback',
    'Roles',
    'Models',
    'Settings',
    'API Keys',
    'Bots',
    'Email',
    'SSO',
    'Audit',
    'Health',
  ]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test('a plain admin with only users.read sees only the Users card', () => {
  mockAuth({ role: 'admin', permissions: ['users.read'] });
  renderHub();
  expect(screen.getByText('Users')).toBeInTheDocument();
  expect(screen.queryByText('Roles')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
  expect(screen.queryByText('Settings')).not.toBeInTheDocument();
  expect(screen.queryByText('API Keys')).not.toBeInTheDocument();
  expect(screen.queryByText('Bots')).not.toBeInTheDocument();
  expect(screen.queryByText('Email')).not.toBeInTheDocument();
  expect(screen.queryByText('SSO')).not.toBeInTheDocument();
  expect(screen.queryByText('Health')).not.toBeInTheDocument();
  expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
  expect(screen.queryByText('Feedback')).not.toBeInTheDocument();
});

test('a user with delegated audit.read sees the Audit card but not Users', () => {
  mockAuth({ role: 'user', permissions: ['audit.read'] });
  renderHub();
  expect(screen.getByText('Audit')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
});

test('typing in the search box filters the visible cards by label and description', async () => {
  mockAuth({ role: 'superadmin' });
  renderHub();
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText('Search settings…'), 'catalog sync');
  expect(screen.getByText('Models')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Bots')).not.toBeInTheDocument();
});

test('search with no matches shows an empty state', async () => {
  mockAuth({ role: 'superadmin' });
  renderHub();
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText('Search settings…'), 'zzzznomatch');
  expect(screen.getByText(/no matching/i)).toBeInTheDocument();
});

test('each card links to the correct admin route', () => {
  mockAuth({ role: 'superadmin' });
  renderHub();
  expect(screen.getByRole('link', { name: /users/i })).toHaveAttribute('href', '/admin/users');
  expect(screen.getByRole('link', { name: /^models/i })).toHaveAttribute('href', '/admin/models');
  expect(screen.getByRole('link', { name: /settings/i })).toHaveAttribute(
    'href',
    '/admin/settings',
  );
  expect(screen.getByRole('link', { name: /api keys/i })).toHaveAttribute(
    'href',
    '/admin/api-keys',
  );
  expect(screen.getByRole('link', { name: /^bots/i })).toHaveAttribute('href', '/admin/bots');
  expect(screen.getByRole('link', { name: /^email/i })).toHaveAttribute('href', '/admin/email');
  expect(screen.getByRole('link', { name: /^sso/i })).toHaveAttribute('href', '/admin/sso');
  expect(screen.getByRole('link', { name: /^audit/i })).toHaveAttribute('href', '/admin/audit');
  expect(screen.getByRole('link', { name: /^health/i })).toHaveAttribute('href', '/admin/health');
  expect(screen.getByRole('link', { name: /^dashboard/i })).toHaveAttribute(
    'href',
    '/admin/dashboard',
  );
  expect(screen.getByRole('link', { name: /^feedback/i })).toHaveAttribute(
    'href',
    '/admin/feedback',
  );
  expect(screen.getByRole('link', { name: /^roles/i })).toHaveAttribute('href', '/admin/roles');
});

test('renders an "Admin" heading', () => {
  mockAuth({ role: 'superadmin' });
  renderHub();
  expect(screen.getByRole('heading', { name: 'Admin' })).toBeInTheDocument();
});
