import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { WorkspaceOut } from '@/api/types';

import { WorkspaceSettingsDialog } from './workspace-settings-dialog';

const ws: WorkspaceOut = {
  id: 'w1',
  name: 'Finance',
  embedding_model: 'bge-m3',
  min_score: 0.35,
  default_model_id: null,
  top_k: 8,
  rerank_enabled: false,
  system_prompt_override: null,
};

function stubFetch(responseBody: WorkspaceOut) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    ),
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
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  // openapi-fetch invokes fetch(Request) with a single argument.
  const req = vi.mocked(fetch).mock.calls[0]![0] as Request;
  expect(req.method).toBe('PATCH');
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
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  const req = vi.mocked(fetch).mock.calls[0]![0] as Request;
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
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  const req = vi.mocked(fetch).mock.calls[0]![0] as Request;
  expect(req.method).toBe('PATCH');
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ top_k: 9 });
});

test('closes without PATCHing when nothing changed', async () => {
  const user = userEvent.setup();
  renderDialog();
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  expect(fetch).not.toHaveBeenCalled();
});

test('clearing the system prompt override sends an explicit null only when it changed', async () => {
  const user = userEvent.setup();
  const withOverride: WorkspaceOut = { ...ws, system_prompt_override: 'Answer tersely.' };
  renderDialog(withOverride, { ...withOverride, system_prompt_override: null });
  const textarea = screen.getByLabelText('System prompt additions');
  await user.clear(textarea);
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  const req = vi.mocked(fetch).mock.calls[0]![0] as Request;
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({ system_prompt_override: null });
});
