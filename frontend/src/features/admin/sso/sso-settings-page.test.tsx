import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { AdminOrg, SsoConfig } from './queries';

const useSsoConfig = vi.fn();
const usePutSsoConfig = vi.fn();
const useAdminOrgs = vi.fn();
const usePutOrgSsoDomains = vi.fn();
vi.mock('./queries', () => ({
  useSsoConfig: () => useSsoConfig(),
  usePutSsoConfig: () => usePutSsoConfig(),
  useAdminOrgs: () => useAdminOrgs(),
  usePutOrgSsoDomains: () => usePutOrgSsoDomains(),
}));

import { SsoSettingsPage } from './sso-settings-page';

const ssoConfig: SsoConfig = {
  issuer: 'https://accounts.google.com',
  client_id: 'client-123.apps.googleusercontent.com',
  client_secret_set: false,
};

const orgs: AdminOrg[] = [
  { id: 'org-1', name: 'Acme', sso_domains: ['acme.com'] },
  { id: 'org-2', name: 'Globex', sso_domains: null },
];

const putSsoSpy = vi.fn();
const putDomainsSpy = vi.fn();

beforeEach(() => {
  useSsoConfig.mockReturnValue({
    data: ssoConfig,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  usePutSsoConfig.mockReturnValue({
    mutate: putSsoSpy,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: false,
  });
  useAdminOrgs.mockReturnValue({
    data: orgs,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  usePutOrgSsoDomains.mockReturnValue({
    mutate: putDomainsSpy,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: false,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders the issuer and client id, with the secret field blank', async () => {
  render(<SsoSettingsPage />);

  expect(await screen.findByLabelText(/issuer/i)).toHaveValue('https://accounts.google.com');
  expect(screen.getByLabelText(/client id/i)).toHaveValue(
    'client-123.apps.googleusercontent.com',
  );
  expect(screen.getByLabelText(/client secret/i)).toHaveAttribute('type', 'password');
  expect(screen.getByLabelText(/client secret/i)).toHaveValue('');
});

test('shows a dotted placeholder and set hint when a secret already exists', async () => {
  useSsoConfig.mockReturnValue({
    data: { ...ssoConfig, client_secret_set: true },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  render(<SsoSettingsPage />);

  const secret = screen.getByLabelText(/client secret/i);
  expect(secret).toHaveAttribute('placeholder', '••••••••');
  // The secret itself is never rendered -- the field stays blank even though
  // the backend reports one is set; only the existence hint shows.
  expect(secret).toHaveValue('');
  expect(screen.getByText(/leave blank to keep/i)).toBeInTheDocument();
});

test('saving with a blank secret omits client_secret from the PUT body', async () => {
  const user = userEvent.setup();
  render(<SsoSettingsPage />);

  await user.click(screen.getAllByRole('button', { name: /^save$/i })[0]!);

  expect(putSsoSpy).toHaveBeenCalledTimes(1);
  const body = putSsoSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.client_secret).toBeUndefined();
  expect(body).toMatchObject({
    issuer: 'https://accounts.google.com',
    client_id: 'client-123.apps.googleusercontent.com',
  });
});

test('typing a secret includes it in the PUT body', async () => {
  const user = userEvent.setup();
  render(<SsoSettingsPage />);

  await user.type(screen.getByLabelText(/client secret/i), 's3cret');
  await user.click(screen.getAllByRole('button', { name: /^save$/i })[0]!);

  expect(putSsoSpy).toHaveBeenCalledTimes(1);
  const body = putSsoSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.client_secret).toBe('s3cret');
});

test('renders orgs and saves domains parsed into a string array', async () => {
  const user = userEvent.setup();
  render(<SsoSettingsPage />);

  const acme = screen.getByLabelText(/acme/i);
  expect(acme).toHaveValue('acme.com');
  expect(screen.getByLabelText(/globex/i)).toHaveValue('');

  await user.clear(acme);
  await user.type(acme, 'Acme.com, acme.com, foo.com , ');
  // The Save button lives in Acme's row, next to its input.
  await user.click(acme.closest('div')?.querySelector('button') as HTMLElement);

  expect(putDomainsSpy).toHaveBeenCalledTimes(1);
  expect(putDomainsSpy).toHaveBeenCalledWith({
    orgId: 'org-1',
    domains: ['acme.com', 'foo.com'],
  });
});

test('shows an error and retry when the SSO query fails', async () => {
  const refetch = vi.fn();
  useSsoConfig.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load SSO settings'),
    refetch,
  });

  render(<SsoSettingsPage />);

  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
