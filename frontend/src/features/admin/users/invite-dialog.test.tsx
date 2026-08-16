import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setAccessToken } from '@/lib/auth-store';

import type { Organization } from '../organizations/queries';

const useOrganizations = vi.fn();
vi.mock('../organizations/queries', () => ({
  useOrganizations: (...args: unknown[]) => useOrganizations(...args),
}));

import { InviteDialog } from './invite-dialog';

const orgA: Organization = { id: 'o1', name: 'Acme Corp', sso_domains: null };
const orgB: Organization = { id: 'o2', name: 'Globex', sso_domains: null };

const b64 = (o: object) =>
  btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (role: string, org = 'o1') =>
  `${b64({ alg: 'HS256' })}.${b64({ sub: 'u1', org, role, exp: 9e9 })}.s`;

function renderDialog(props: Partial<React.ComponentProps<typeof InviteDialog>> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ invite_token: 'raw-tok-123' }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InviteDialog open onOpenChange={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useOrganizations.mockReturnValue({ data: [orgA, orgB], isPending: false, isError: false });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  setAccessToken(null);
});

test('creates an invitation and reveals the one-time link', async () => {
  const user = userEvent.setup();
  renderDialog();
  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.selectOptions(screen.getByLabelText('Role'), 'admin');
  await user.click(screen.getByRole('button', { name: 'Send invite' }));
  const link = await screen.findByText(/\/invite\?token=raw-tok-123/);
  expect(link).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument();
});

test('a non-superadmin sees no organization selector and sends no org_id', async () => {
  setAccessToken(tokenFor('admin'));
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify({ invite_token: 'raw-tok-123' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InviteDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );

  expect(screen.queryByLabelText('Organization')).not.toBeInTheDocument();

  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.click(screen.getByRole('button', { name: 'Send invite' }));
  await screen.findByText(/\/invite\?token=raw-tok-123/);

  const [request] = fetchMock.mock.calls[0] as unknown as [Request];
  const body = JSON.parse(await request.clone().text()) as Record<string, unknown>;
  expect(body).not.toHaveProperty('org_id');
});

test('a superadmin sees an organization selector defaulting to their own org and it is sent in the payload', async () => {
  setAccessToken(tokenFor('superadmin', 'o2'));
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify({ invite_token: 'raw-tok-123' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InviteDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );

  const orgSelect = screen.getByLabelText('Organization');
  expect(orgSelect).toHaveValue('o2');

  await user.selectOptions(orgSelect, 'o1');
  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.click(screen.getByRole('button', { name: 'Send invite' }));
  await screen.findByText(/\/invite\?token=raw-tok-123/);

  const [request] = fetchMock.mock.calls[0] as unknown as [Request];
  const body = JSON.parse(await request.clone().text()) as Record<string, unknown>;
  expect(body).toMatchObject({ org_id: 'o1' });
});

test('defaultOrgId and defaultRole seed the fields for a superadmin', async () => {
  setAccessToken(tokenFor('superadmin', 'o2'));
  renderDialog({ defaultOrgId: 'o1', defaultRole: 'admin' });

  expect(screen.getByLabelText('Organization')).toHaveValue('o1');
  expect(screen.getByLabelText('Role')).toHaveValue('admin');
});
