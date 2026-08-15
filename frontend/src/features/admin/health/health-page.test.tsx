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
  db: { status: 'ok', latency_ms: 4 },
  redis: { status: 'ok', latency_ms: 2 },
  queues: { status: 'ok', depths: { default: 2, interactive: 0 } },
  qdrant: { status: 'ok', collections: [{ name: 'org_docs', points_count: 4200 }] },
  minio: { status: 'ok', latency_ms: 11 },
  embedder: { status: 'ok', latency_ms: 40 },
  reranker: { status: 'ok', latency_ms: 55 },
  litellm: { status: 'ok' },
  orgs: [{ org_id: 'org-1', name: 'Acme', tokens: 12_345 }],
};

const degradedHealth: SystemHealth = {
  ...healthyHealth,
  qdrant: { status: 'error', detail: 'ConnectError' },
  reranker: { status: 'error', detail: 'ConnectError', latency_ms: 8 },
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

test('renders a dependency row per new probe (db/redis/minio/embedder/reranker) with status + latency', () => {
  useSystemHealth.mockReturnValue({ data: healthyHealth, isPending: false });
  useClientErrors.mockReturnValue({ data: [], isPending: false });

  render(<HealthPage />);

  for (const [label, latency] of [
    ['Postgres', '4 ms'],
    ['Redis', '2 ms'],
    ['Object storage (MinIO)', '11 ms'],
    ['Embedder (TEI)', '40 ms'],
    ['Reranker (TEI)', '55 ms'],
  ] as const) {
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(latency)).toBeInTheDocument();
  }
  // healthy deps render the OK badge, visible text (not color-only)
  expect(screen.getAllByText('OK')).toHaveLength(5);
});

test('a down reranker renders an Error badge for that dependency while others stay OK', () => {
  useSystemHealth.mockReturnValue({ data: degradedHealth, isPending: false });
  useClientErrors.mockReturnValue({ data: [], isPending: false });

  render(<HealthPage />);

  expect(screen.getAllByText('Error')).toHaveLength(1);
  expect(screen.getAllByText('OK')).toHaveLength(4);
});

test('a slow (high-latency) but healthy dependency is visually flagged distinctly from a normal one', () => {
  const slowHealth: SystemHealth = {
    ...healthyHealth,
    embedder: { status: 'ok', latency_ms: 12_000 },
  };
  useSystemHealth.mockReturnValue({ data: slowHealth, isPending: false });
  useClientErrors.mockReturnValue({ data: [], isPending: false });

  render(<HealthPage />);

  const slowLatency = screen.getByText('12,000 ms (slow)');
  expect(slowLatency.className).toContain('text-warning');
  const normalLatency = screen.getByText('2 ms');
  expect(normalLatency.className).not.toContain('text-warning');
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
