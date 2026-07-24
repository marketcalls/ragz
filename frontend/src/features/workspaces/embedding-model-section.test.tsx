import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { toast } from '@/components/ui/toaster';

import { EmbeddingModelSection } from './embedding-model-section';

function jsonResponse(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: body === null ? undefined : { 'content-type': 'application/json' },
  });
}

const MODEL_A = {
  id: 'model-a',
  litellm_model_name: 'text-embedding-a',
  display_name: 'Embedding A',
  provider_kind: 'openai',
  base_url: null,
  enabled: true,
  key_fingerprint: null,
  sync_status: 'synced',
  mock_response: null,
  tools_unreliable: false,
  supports_reasoning: false,
  default_reasoning_effort: 'off',
  supports_vision: false,
  is_utility: false,
  modality: 'embedding',
  dimension: 384,
  collection_name: 'col_a',
};

const MODEL_B = {
  ...MODEL_A,
  id: 'model-b',
  display_name: 'Embedding B',
  collection_name: 'col_b',
};

function renderSection(fetchMock: ReturnType<typeof vi.fn>, client?: QueryClient) {
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <EmbeddingModelSection workspaceId="w1" currentModelId="model-a" />
    </QueryClientProvider>,
  );
  return queryClient;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('selecting a different embedding model on an empty workspace saves immediately', async () => {
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/embedding-model')) return jsonResponse({ ...MODEL_A });
    return jsonResponse(null, 404);
  });
  const user = userEvent.setup();
  renderSection(fetchMock);

  const select = await screen.findByLabelText('Embedding model');
  await screen.findByRole('option', { name: 'Embedding B' });
  await user.selectOptions(select, 'model-b');

  await waitFor(() => expect(toast).toHaveBeenCalledWith('Embedding model updated'));
  expect(screen.queryByText(/already has indexed documents/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Re-embed now' })).not.toBeInTheDocument();
});

test('a 409 response shows the lock message and Re-embed now button instead of failing silently', async () => {
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/embedding-model')) {
      return jsonResponse({ detail: 'workspace already has indexed documents' }, 409);
    }
    return jsonResponse(null, 404);
  });
  const user = userEvent.setup();
  renderSection(fetchMock);

  const select = await screen.findByLabelText('Embedding model');
  await screen.findByRole('option', { name: 'Embedding B' });
  await user.selectOptions(select, 'model-b');

  expect(await screen.findByText(/already has indexed documents/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Re-embed now' })).toBeInTheDocument();
  // Must not have been reported as a generic failure toast.
  expect(toast.error).not.toHaveBeenCalled();
});

test('clicking Re-embed now starts the job and the progress display tracks status until finished', async () => {
  const RUNNING: {
    id: string;
    workspace_id: string;
    old_embedding_model_id: string;
    new_embedding_model_id: string;
    documents_total: number;
    documents_done: number;
    error: string | null;
    finished_at: string | null;
  } = {
    id: 'job-1',
    workspace_id: 'w1',
    old_embedding_model_id: 'model-a',
    new_embedding_model_id: 'model-b',
    documents_total: 4,
    documents_done: 1,
    error: null,
    finished_at: null,
  };
  const FINISHED = { ...RUNNING, documents_done: 4, finished_at: '2026-07-24T00:00:00Z' };

  let statusResponse = RUNNING;
  let reembedCalled = false;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/embedding-model')) {
      return jsonResponse({ detail: 'workspace already has indexed documents' }, 409);
    }
    if (url.includes('/reembed-status')) return jsonResponse(statusResponse);
    if (url.includes('/reembed')) {
      reembedCalled = true;
      return jsonResponse(RUNNING, 202);
    }
    return jsonResponse(null, 404);
  });
  const user = userEvent.setup();
  const client = renderSection(fetchMock);

  const select = await screen.findByLabelText('Embedding model');
  await screen.findByRole('option', { name: 'Embedding B' });
  await user.selectOptions(select, 'model-b');

  await user.click(await screen.findByRole('button', { name: 'Re-embed now' }));

  expect(reembedCalled).toBe(true);
  // Polling kicks in once useStartReembed succeeds and pendingModelId is set.
  await waitFor(() =>
    expect(screen.getByText(/Re-embedding: 1 \/ 4 documents/)).toBeInTheDocument(),
  );
  // Lock banner is dismissed once the re-embed is confirmed.
  expect(screen.queryByText(/already has indexed documents/)).not.toBeInTheDocument();

  // Simulate the next poll tick returning a finished job.
  statusResponse = FINISHED;
  await client.refetchQueries({ queryKey: ['reembed-status', 'w1'] });

  await waitFor(() => expect(screen.queryByText(/Re-embedding:/)).not.toBeInTheDocument());
});
