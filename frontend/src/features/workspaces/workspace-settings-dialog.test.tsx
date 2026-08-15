import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { WorkspaceOut } from '@/api/types';

import { WorkspaceSettingsDialog } from './workspace-settings-dialog';

const ws: WorkspaceOut = {
  id: 'w1',
  name: 'Finance',
  embedding_model_id: 'model-embed-1',
  min_score: 0.35,
  default_model_id: null,
  top_k: 8,
  rerank_enabled: false,
  system_prompt_override: null,
  fallback_policy: 'general_knowledge',
  web_search_enabled: false,
  strict_mode: false,
  enrichment_enabled: false,
  chunk_method: 'heading',
};

function stubFetch(responseBody: WorkspaceOut) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (req: Request) => {
      // MetadataFieldsSection (H-C8 mount point) fetches its own field list
      // on mount — stub it to an empty schema so it doesn't interfere with
      // these settings-form assertions.
      if (req.url.includes('/metadata-fields')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      // EmbeddingModelSection (DOC-10 mount point) fetches the admin model
      // list on mount — stub it to an empty list so it doesn't interfere
      // with these settings-form assertions.
      if (req.url.includes('/admin/models')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      // EmbeddingModelSection also fetches /reembed-status unconditionally on
      // mount now (fix round: it must not depend on local dialog state to
      // stay visible across dialog remounts) — stub a clean 404, matching a
      // workspace that has never run a re-embed job.
      if (req.url.includes('/reembed-status')) {
        return new Response(null, { status: 404 });
      }
      return new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
}

function renderDialog(workspace: WorkspaceOut = ws, responseBody: WorkspaceOut = ws) {
  stubFetch(responseBody);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceSettingsDialog workspace={workspace} open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

// MetadataFieldsSection now shares this dialog and fires its own GET on
// mount, so "the PATCH request" is no longer necessarily fetch call #0 —
// find it by method instead.
function findPatch(): Request {
  const call = vi.mocked(fetch).mock.calls.find(([req]) => (req as Request).method === 'PATCH');
  if (!call) throw new Error('no PATCH request was made');
  return call[0] as Request;
}

afterEach(() => vi.unstubAllGlobals());

test('shows current values and PATCHes only the edited settings', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, top_k: 12, rerank_enabled: true });
  const topK = screen.getByLabelText('Sources per query (top_k)');
  expect(topK).toHaveValue(8);
  await user.clear(topK);
  await user.type(topK, '12');
  await user.click(screen.getByLabelText('Rerank with cross-encoder'));
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  // openapi-fetch invokes fetch(Request) with a single argument.
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  // Only the two fields the user actually touched are sent — untouched fields
  // (min_score, system_prompt_override) must be absent, not just unchanged, so
  // the backend's partial-update (model_fields_set) contract isn't defeated.
  expect(body).toStrictEqual({ top_k: 12, rerank_enabled: true });
});

test('leaves an untouched field out of the PATCH body entirely', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, top_k: 12 });
  const topK = screen.getByLabelText('Sources per query (top_k)');
  await user.clear(topK);
  await user.type(topK, '12');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ top_k: 12 });
  expect('min_score' in body).toBe(false);
  expect('rerank_enabled' in body).toBe(false);
  expect('system_prompt_override' in body).toBe(false);
});

test('submits successfully when min_score is a non-step-aligned value and only top_k changes', async () => {
  const user = userEvent.setup();
  const misaligned: WorkspaceOut = { ...ws, min_score: 0.33 };
  renderDialog(misaligned, { ...misaligned, top_k: 9 });
  const topK = screen.getByLabelText('Sources per query (top_k)');
  await user.clear(topK);
  await user.type(topK, '9');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  // With step="any" the 0.33-valued min_score input never blocks native form
  // validation, so the PATCH fires.
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ top_k: 9 });
});

test('closes without PATCHing when nothing changed', async () => {
  const user = userEvent.setup();
  renderDialog();
  // MetadataFieldsSection's own GET still fires on mount — only the
  // workspace-settings PATCH must stay suppressed.
  await screen.findByText('No metadata fields yet.');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
    false,
  );
});

test('changing the fallback policy PATCHes only fallback_policy', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, fallback_policy: 'decline' });
  await user.selectOptions(screen.getByLabelText('If retrieval finds nothing'), 'decline');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ fallback_policy: 'decline' });
});

test('leaves fallback_policy out of the PATCH body when untouched', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, top_k: 12 });
  const topK = screen.getByLabelText('Sources per query (top_k)');
  await user.clear(topK);
  await user.type(topK, '12');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect('fallback_policy' in body).toBe(false);
});

test('the web search toggle is visible and submits web_search_enabled when changed (Task 11/D7)', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, web_search_enabled: true });
  const toggle = screen.getByLabelText('Allow web search');
  expect(toggle).not.toBeChecked();
  await user.click(toggle);
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ web_search_enabled: true });
});

test('the web search toggle is hidden when the fallback policy is decline (spec D7)', () => {
  renderDialog({ ...ws, fallback_policy: 'decline' });
  expect(screen.queryByLabelText('Allow web search')).not.toBeInTheDocument();
});

test('checking strict mode PATCHes only strict_mode', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, strict_mode: true });
  const toggle = screen.getByLabelText('Strict mode');
  expect(toggle).not.toBeChecked();
  await user.click(toggle);
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ strict_mode: true });
});

test('leaves strict_mode out of the PATCH body when untouched', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, top_k: 12 });
  const topK = screen.getByLabelText('Sources per query (top_k)');
  await user.clear(topK);
  await user.type(topK, '12');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect('strict_mode' in body).toBe(false);
});

test('checking enrichment PATCHes only enrichment_enabled', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, enrichment_enabled: true });
  const toggle = screen.getByLabelText('Enable search-recall enrichment');
  expect(toggle).not.toBeChecked();
  await user.click(toggle);
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ enrichment_enabled: true });
});

test('leaves enrichment_enabled out of the PATCH body when untouched', async () => {
  const user = userEvent.setup();
  renderDialog(ws, { ...ws, top_k: 12 });
  const topK = screen.getByLabelText('Sources per query (top_k)');
  await user.clear(topK);
  await user.type(topK, '12');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect('enrichment_enabled' in body).toBe(false);
});

test('clearing the system prompt override sends an explicit null only when it changed', async () => {
  const user = userEvent.setup();
  const withOverride: WorkspaceOut = { ...ws, system_prompt_override: 'Answer tersely.' };
  renderDialog(withOverride, { ...withOverride, system_prompt_override: null });
  const textarea = screen.getByLabelText('System prompt additions');
  await user.clear(textarea);
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([req]) => (req as Request).method === 'PATCH')).toBe(
      true,
    ),
  );
  const req = findPatch();
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ system_prompt_override: null });
});
