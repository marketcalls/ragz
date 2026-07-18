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

function renderDialog() {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ ...ws, top_k: 12, rerank_enabled: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceSettingsDialog workspace={ws} open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('shows current values and PATCHes the edited settings', async () => {
  const user = userEvent.setup();
  renderDialog();
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
  expect(body).toMatchObject({ top_k: 12, rerank_enabled: true, min_score: 0.35 });
  expect(body.system_prompt_override).toBeNull(); // empty textarea -> explicit clear
});
