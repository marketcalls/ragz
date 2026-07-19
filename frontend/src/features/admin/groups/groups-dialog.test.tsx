import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { GroupsDialog } from './groups-dialog';

function renderDialog(fetchMock = vi.fn()) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GroupsDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

test('renders the group list from a stubbed GET /api/v1/groups', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify([{ id: 'g1', name: 'finance', member_ids: ['u1'] }]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  renderDialog(fetchMock);
  expect(await screen.findByText('finance', { exact: false })).toBeInTheDocument();
  expect(screen.getByText('(1 member)')).toBeInTheDocument();
});

test('submits the create form and POSTs the new group name', async () => {
  const fetchMock = vi.fn(async (req: Request) => {
    if (req.method === 'POST') {
      return new Response(JSON.stringify({ id: 'g2', name: 'finance', member_ids: [] }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);
  await screen.findByText('No groups yet.');
  await user.type(screen.getByLabelText('New group name'), 'finance');
  await user.click(screen.getByRole('button', { name: 'Create' }));

  await vi.waitFor(() =>
    expect(fetchMock.mock.calls.some(([req]: [Request]) => req.method === 'POST')).toBe(true),
  );
  const postCall = fetchMock.mock.calls.find(([req]: [Request]) => req.method === 'POST');
  const req = postCall![0] as Request;
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toEqual({ name: 'finance' });
});
