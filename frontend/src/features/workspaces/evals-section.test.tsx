import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { EvalsSection } from './evals-section';

const DOC = {
  id: 'd1',
  filename: 'policy.pdf',
  mime: 'application/pdf',
  size_bytes: 1024,
  status: 'indexed',
  page_count: 3,
  error: null,
  created_at: '2026-07-18T00:00:00Z',
  pinned: false,
  version: 1,
  lineage_id: 'd1',
  is_current: true,
  approved: false,
  supersedes_document_id: null,
  meta: {},
};

const GOLDEN_QUERY = {
  id: 'g1',
  workspace_id: 'w1',
  question: 'Where is the muster point?',
  expected_document_ids: ['d1'],
  created_by: 'u1',
  created_at: '2026-07-18T00:00:00Z',
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: body === null ? undefined : { 'content-type': 'application/json' },
  });
}

function renderSection(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <EvalsSection workspaceId="w1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('question textarea has an accessible name', async () => {
  const fetchMock = vi.fn(async () => jsonResponse([]));
  renderSection(fetchMock);

  const textarea = await screen.findByLabelText(/question/i);
  expect(textarea).toBeInTheDocument();
  expect(textarea.tagName).toBe('TEXTAREA');
  expect(textarea).toHaveAttribute('maxlength', '2000');
});

test('creates a golden query with selected expected documents and lists it', async () => {
  let created = false;
  let capturedBody: unknown = null;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/documents')) {
      return jsonResponse([DOC]);
    }
    if (url.includes('/golden-queries') && req.method === 'POST') {
      capturedBody = await req.clone().json();
      created = true;
      return jsonResponse(GOLDEN_QUERY, 201);
    }
    if (url.includes('/golden-queries')) {
      return jsonResponse(created ? [GOLDEN_QUERY] : []);
    }
    return jsonResponse([]);
  });

  const user = userEvent.setup();
  renderSection(fetchMock);

  expect(await screen.findByText('policy.pdf')).toBeInTheDocument();

  await user.type(screen.getByLabelText(/question/i), 'Where is the muster point?');
  await user.click(screen.getByRole('checkbox', { name: 'policy.pdf' }));
  await user.click(screen.getByRole('button', { name: 'Add golden query' }));

  await waitFor(() =>
    expect(capturedBody).toEqual({
      question: 'Where is the muster point?',
      expected_document_ids: ['d1'],
    }),
  );
  expect(await screen.findByText('Where is the muster point?')).toBeInTheDocument();
});

test('deletes a golden query after confirm', async () => {
  let deletedId: string | null = null;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/documents')) {
      return jsonResponse([]);
    }
    if (req.method === 'DELETE' && url.includes('/golden-queries/')) {
      deletedId = url.split('/golden-queries/')[1] ?? null;
      return jsonResponse(null, 204);
    }
    if (url.includes('/golden-queries')) {
      return jsonResponse(deletedId ? [] : [GOLDEN_QUERY]);
    }
    return jsonResponse([]);
  });

  const user = userEvent.setup();
  renderSection(fetchMock);

  expect(await screen.findByText('Where is the muster point?')).toBeInTheDocument();

  await user.click(
    screen.getByRole('button', { name: 'Delete golden query: Where is the muster point?' }),
  );
  await user.click(screen.getByRole('button', { name: 'Delete' }));

  await waitFor(() => expect(deletedId).toBe('g1'));
  await waitFor(() =>
    expect(screen.queryByText('Where is the muster point?')).not.toBeInTheDocument(),
  );
});
