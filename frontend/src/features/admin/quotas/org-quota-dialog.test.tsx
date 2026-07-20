import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { OrgQuotaDialog } from './org-quota-dialog';

const fixtureQuota = {
  org_id: 'org-1',
  monthly_tokens: 100_000,
  default_user_monthly_tokens: 5_000,
  reset_day: 1,
};

function renderDialog(
  fetchMock: ReturnType<typeof vi.fn>,
  options: { onOpenChange?: (open: boolean) => void } = {},
) {
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = options.onOpenChange ?? vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <OrgQuotaDialog
        open
        onOpenChange={onOpenChange}
        orgId="org-1"
        orgName="Acme"
        usageTokens={12_345}
      />
    </QueryClientProvider>,
  );
  return { onOpenChange };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('shows current usage and pre-fills the form from the existing org quota', async () => {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(fixtureQuota), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  renderDialog(fetchMock);

  expect(await screen.findByText(/12,345 tokens/)).toBeInTheDocument();
  expect(screen.getByLabelText('Monthly tokens')).toHaveValue(100_000);
  expect(screen.getByLabelText('Default user allocation')).toHaveValue(5_000);
  expect(screen.getByLabelText('Reset day')).toHaveValue(1);
});

test('pre-fills blank fields when the org has no quota row yet', async () => {
  const fetchMock = vi.fn(async () =>
    new Response('null', { status: 200, headers: { 'content-type': 'application/json' } }),
  );
  renderDialog(fetchMock);

  expect(await screen.findByLabelText('Monthly tokens')).toHaveValue(null);
  expect(screen.getByLabelText('Default user allocation')).toHaveValue(null);
  expect(screen.getByLabelText('Reset day')).toHaveValue(1);
});

test('submitting PUTs the form values and closes the dialog', async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async (req: Request) => {
    if (req.method === 'PUT') {
      return new Response(JSON.stringify({ ...fixtureQuota, monthly_tokens: 200_000 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response(JSON.stringify(fixtureQuota), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  const { onOpenChange } = renderDialog(fetchMock);

  await screen.findByLabelText('Monthly tokens');
  await user.clear(screen.getByLabelText('Monthly tokens'));
  await user.type(screen.getByLabelText('Monthly tokens'), '200000');
  await user.click(screen.getByRole('button', { name: 'Save' }));

  await vi.waitFor(() =>
    expect(fetchMock.mock.calls.some(([req]: [Request]) => req.method === 'PUT')).toBe(true),
  );
  const putCall = fetchMock.mock.calls.find(([req]: [Request]) => req.method === 'PUT');
  const req = putCall![0] as Request;
  expect(req.url).toContain('/api/v1/admin/orgs/org-1/quota');
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toEqual({
    monthly_tokens: 200_000,
    default_user_monthly_tokens: 5_000,
    reset_day: 1,
  });
  await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});

test('shows an inline error message when saving fails', async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async (req: Request) => {
    if (req.method === 'PUT') {
      return new Response(JSON.stringify({ detail: 'boom' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response(JSON.stringify(fixtureQuota), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  renderDialog(fetchMock);

  await screen.findByLabelText('Monthly tokens');
  await user.click(screen.getByRole('button', { name: 'Save' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to save/i);
});
