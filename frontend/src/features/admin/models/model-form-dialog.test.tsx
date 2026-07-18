import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ModelOut } from '@/api/types';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { toast } from '@/components/ui/toaster';

import { ModelFormDialog } from './model-form-dialog';

const fixtureModel: ModelOut = {
  id: 'm1',
  litellm_model_name: 'gpt-4o-mini',
  display_name: 'GPT-4o mini',
  provider_kind: 'openai',
  base_url: null,
  enabled: true,
  key_fingerprint: 'ab12…ef90',
  sync_status: 'synced',
};

function renderDialog(
  fetchMock = vi.fn(),
  options: {
    model?: ModelOut | null;
    onOpenChange?: (open: boolean) => void;
    queryClient?: QueryClient;
  } = {},
) {
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = options.queryClient ?? new QueryClient();
  const onOpenChange = options.onOpenChange ?? vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <ModelFormDialog open onOpenChange={onOpenChange} model={options.model ?? null} />
    </QueryClientProvider>,
  );
  return { queryClient, onOpenChange };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('base URL appears only for ollama and openai_compatible', async () => {
  const user = userEvent.setup();
  renderDialog();
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument(); // openai default
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Provider'), 'openai_compatible');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
});

test('api key is a write-only password field, absent for ollama', async () => {
  const user = userEvent.setup();
  renderDialog();
  const key = screen.getByLabelText('API key');
  expect(key).toHaveAttribute('type', 'password');
  expect(key).toHaveAttribute('autocomplete', 'off');
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
});

test('submits the assembled payload', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify({ id: 'm1', key_fingerprint: 'ab12…ef90' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock);
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('API key'), 'sk-test-123');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const req = fetchMock.mock.calls[0]![0] as Request;
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toMatchObject({
    display_name: 'GPT-4o mini',
    litellm_model_name: 'gpt-4o-mini',
    provider_kind: 'openai',
    api_key: 'sk-test-123',
  });
});

test('a 502 still invalidates the caches and closes the dialog as a partial success', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify({ detail: 'gateway unreachable' }), {
      status: 502,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const queryClient = new QueryClient();
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const user = userEvent.setup();
  const { onOpenChange } = renderDialog(fetchMock, { queryClient });
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  // The local write succeeded (only the gateway sync failed) — the dialog closes.
  await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['admin-models'] });
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['models'] });
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('gateway sync failed'));
});

test('a non-502 failure keeps the dialog open with a distinguishable generic message, but still invalidates', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify({ detail: 'boom' }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const queryClient = new QueryClient();
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const user = userEvent.setup();
  const { onOpenChange } = renderDialog(fetchMock, { queryClient });
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('request failed');
  expect(onOpenChange).not.toHaveBeenCalled();
  await vi.waitFor(() =>
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['admin-models'] }),
  );
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['models'] });
});

test('edit mode: provider and model id are read-only, API key starts blank with a fingerprint hint', () => {
  renderDialog(vi.fn(), { model: fixtureModel });
  expect(screen.getByLabelText('Provider')).toBeDisabled();
  expect(screen.getByLabelText('Model id')).toBeDisabled();
  const key = screen.getByLabelText('API key') as HTMLInputElement;
  expect(key.value).toBe('');
  expect(key.getAttribute('placeholder')).toContain('ab12…ef90');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
});

test('edit mode PATCHes only the fields that changed', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify({ id: 'm1', key_fingerprint: 'ab12…ef90' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock, { model: fixtureModel });
  await user.clear(screen.getByLabelText('Display name'));
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini v2');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const req = fetchMock.mock.calls[0]![0] as Request;
  expect(req.method).toBe('PATCH');
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toEqual({ display_name: 'GPT-4o mini v2' });
});
