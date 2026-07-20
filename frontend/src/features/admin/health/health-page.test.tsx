import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ClientErrorOut } from '@/api/types';

import type { SystemHealth } from './queries';

const useSystemHealth = vi.fn();
const useClientErrors = vi.fn();
vi.mock('./queries', () => ({
  useSystemHealth: () => useSystemHealth(),
  useClientErrors: () => useClientErrors(),
}));

// OrgQuotaDialog (Task 15) is only mounted once an org row is picked; its own
// queries are mocked here rather than the dialog component, mirroring how
// users-page.test.tsx mocks GroupsDialog's queries instead of the component.
const useOrgQuota = vi.fn();
const usePutOrgQuota = vi.fn();
vi.mock('../quotas/queries', () => ({
  useOrgQuota: (orgId: string, enabled: boolean) => useOrgQuota(orgId, enabled),
  usePutOrgQuota: () => usePutOrgQuota(),
}));

import { HealthPage } from './health-page';

const healthyHealth: SystemHealth = {
  queues: { status: 'ok', depths: { default: 2, interactive: 0 } },
  qdrant: { status: 'ok', collections: [{ name: 'org_docs', points_count: 4200 }] },
  litellm: { status: 'ok' },
  orgs: [{ org_id: 'org-1', name: 'Acme', tokens: 12_345 }],
};

const degradedHealth: SystemHealth = {
  ...healthyHealth,
  qdrant: { status: 'error', detail: 'ConnectError' },
};

const clientErrors: ClientErrorOut[] = [
  {
    ts: 1_800_000_000,
    org_id: 'org-1',
    user_id: 'user-1',
    message: 'x'.repeat(200),
    stack: null,
    url: 'https://app.example.com/chat',
  },
];

beforeEach(() => {
  useOrgQuota.mockReturnValue({ data: undefined, isPending: false, isError: false });
  usePutOrgQuota.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('degraded qdrant renders a Failed-styled pill with visible text, plus one client-error row', () => {
  useSystemHealth.mockReturnValue({ data: degradedHealth, isPending: false });
  useClientErrors.mockReturnValue({ data: clientErrors, isPending: false });

  render(<HealthPage />);

  const qdrantPill = screen.getByText('Qdrant: Failed');
  expect(qdrantPill.className).toContain('bg-danger-soft');
  expect(qdrantPill.className).toContain('text-danger');

  const liteLlmPill = screen.getByText('LiteLLM: Healthy');
  expect(liteLlmPill.className).toContain('bg-success-soft');
  expect(liteLlmPill.className).toContain('text-success');

  // one client-error row rendered
  expect(screen.getByText('user-1')).toBeInTheDocument();
  expect(screen.getByText('https://app.example.com/chat')).toBeInTheDocument();
});

test('clicking Manage quota on an org row opens the org quota dialog for that org', async () => {
  useSystemHealth.mockReturnValue({ data: healthyHealth, isPending: false });
  useClientErrors.mockReturnValue({ data: [], isPending: false });

  render(<HealthPage />);
  await userEvent.click(screen.getByRole('button', { name: 'Manage quota' }));

  expect(await screen.findByText('Quota — Acme')).toBeInTheDocument();
  expect(useOrgQuota).toHaveBeenCalledWith('org-1', true);
});

test('shows an error message and retry button when the health query fails', async () => {
  const refetch = vi.fn();
  useSystemHealth.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load system health'),
    refetch,
  });
  useClientErrors.mockReturnValue({ data: [], isPending: false });

  render(<HealthPage />);

  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
