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

  // The status query now fires unconditionally on mount (Bug 2 fix), so it
  // must reflect reality: no job exists yet until /reembed is actually
  // called, matching the backend's clean 404 for a workspace that has never
  // run a re-embed job.
  let statusResponse: typeof RUNNING | null = null;
  let reembedCalled = false;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/embedding-model')) {
      return jsonResponse({ detail: 'workspace already has indexed documents' }, 409);
    }
    if (url.includes('/reembed-status')) {
      return statusResponse ? jsonResponse(statusResponse) : jsonResponse(null, 404);
    }
    if (url.includes('/reembed')) {
      reembedCalled = true;
      statusResponse = RUNNING;
      return jsonResponse(RUNNING, 202);
    }
    return jsonResponse(null, 404);
  });
  const user = userEvent.setup();
  const client = renderSection(fetchMock);

  const select = await screen.findByLabelText('Embedding model');
  await screen.findByRole('option', { name: 'Embedding B' });
  // The select must not start out disabled -- no job is running yet.
  expect(select).not.toBeDisabled();
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

// Fix round (task review): a failed job's error was only ever rendered
// nested inside the `running` block, but the backend sets `error` and
// `finished_at` together on failure -- so `running` is already false the
// instant a failed job is polled, and the message never appeared.
test('a failed re-embed job shows its error message even though the progress line is gone', async () => {
  const FAILED = {
    id: 'job-failed',
    workspace_id: 'w1',
    old_embedding_model_id: 'model-a',
    new_embedding_model_id: 'model-b',
    documents_total: 4,
    documents_done: 2,
    error: 'embedding provider timed out',
    finished_at: '2026-07-24T00:00:00Z',
  };
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/reembed-status')) return jsonResponse(FAILED);
    return jsonResponse(null, 404);
  });
  renderSection(fetchMock);

  expect(
    await screen.findByText(/Re-embed failed: embedding provider timed out/),
  ).toBeInTheDocument();
  // The in-progress line must not be present -- the job already finished.
  expect(screen.queryByText(/Re-embedding:/)).not.toBeInTheDocument();
});

// Fix round: the status query used to be gated behind local `pendingModelId`
// state, which resets to null whenever the settings dialog (this
// component's usual host) unmounts and remounts on close/reopen. Simulate
// that scenario directly: mount the component fresh, with no prior lock/
// confirm interaction, against a workspace that already has a job in flight
// from a previous session. The progress line must appear from the very
// first render without the user doing anything.
test('fetches re-embed status on initial mount without requiring the lock-then-confirm flow first', async () => {
  const RUNNING_FROM_PRIOR_SESSION = {
    id: 'job-prior',
    workspace_id: 'w1',
    old_embedding_model_id: 'model-a',
    new_embedding_model_id: 'model-b',
    documents_total: 10,
    documents_done: 3,
    error: null,
    finished_at: null,
  };
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/reembed-status')) return jsonResponse(RUNNING_FROM_PRIOR_SESSION);
    return jsonResponse(null, 404);
  });
  renderSection(fetchMock);

  expect(await screen.findByText(/Re-embedding: 3 \/ 10 documents/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([req]) => (req as Request).url.includes('/reembed-status'))).toBe(
    true,
  );
});

// Fix round (Bug 3): once a job genuinely completes (finished_at set, no
// error), the workspace's currentModelId has changed server-side and the
// ['workspaces'] cache must be invalidated so the dropdown and the stale
// `if (modelId === currentModelId) return;` guard both see the new model.
test('a successful completion invalidates the workspaces cache exactly once', async () => {
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
    id: 'job-success',
    workspace_id: 'w1',
    old_embedding_model_id: 'model-a',
    new_embedding_model_id: 'model-b',
    documents_total: 2,
    documents_done: 1,
    error: null,
    finished_at: null,
  };
  const FINISHED = { ...RUNNING, documents_done: 2, finished_at: '2026-07-24T00:00:00Z' };

  let statusResponse = RUNNING;
  const fetchMock = vi.fn(async (req: Request) => {
    const url = req.url;
    if (url.includes('/admin/models')) return jsonResponse([MODEL_A, MODEL_B]);
    if (url.includes('/reembed-status')) return jsonResponse(statusResponse);
    return jsonResponse(null, 404);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
  renderSection(fetchMock, client);

  await screen.findByText(/Re-embedding: 1 \/ 2 documents/);
  expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['workspaces'] });

  statusResponse = FINISHED;
  await client.refetchQueries({ queryKey: ['reembed-status', 'w1'] });

  await waitFor(() =>
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['workspaces'] }),
  );
  const callsSoFar = invalidateSpy.mock.calls.filter(
    ([arg]) => JSON.stringify(arg) === JSON.stringify({ queryKey: ['workspaces'] }),
  ).length;
  expect(callsSoFar).toBe(1);

  // A later poll tick that re-observes the same finished job must not
  // trigger a second invalidation.
  await client.refetchQueries({ queryKey: ['reembed-status', 'w1'] });
  await waitFor(() => expect(screen.queryByText(/Re-embedding:/)).not.toBeInTheDocument());
  const callsAfter = invalidateSpy.mock.calls.filter(
    ([arg]) => JSON.stringify(arg) === JSON.stringify({ queryKey: ['workspaces'] }),
  ).length;
  expect(callsAfter).toBe(1);
});
