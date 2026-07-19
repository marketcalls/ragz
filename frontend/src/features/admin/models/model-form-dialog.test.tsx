import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { CatalogOut, ModelOut } from '@/api/types';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { toast } from '@/components/ui/toaster';

import { ModelFormDialog, deriveProviderKind, prettifyModelName } from './model-form-dialog';

const fixtureModel: ModelOut = {
  id: 'm1',
  litellm_model_name: 'gpt-4o-mini',
  display_name: 'GPT-4o mini',
  provider_kind: 'openai',
  base_url: null,
  enabled: true,
  key_fingerprint: 'ab12…ef90',
  sync_status: 'synced',
  mock_response: null,
  tools_unreliable: false,
  is_utility: false,
};

// Entries as the API returns them: provider ASC, position DESC (newest first).
const fixtureCatalog: CatalogOut = {
  new_available: 4,
  entries: [
    { name: 'claude-4-sonnet', provider: 'anthropic', max_input_tokens: 200000,
      input_cost_per_1m: 3, output_cost_per_1m: 15, position: 9, registered: false },
    { name: 'claude-3-haiku', provider: 'anthropic', max_input_tokens: 200000,
      input_cost_per_1m: 0.25, output_cost_per_1m: 1.25, position: 2, registered: false },
    { name: 'gemini/gemini-2.5-pro', provider: 'gemini', max_input_tokens: 1048576,
      input_cost_per_1m: 1.25, output_cost_per_1m: 10, position: 7, registered: false },
    { name: 'gpt-4o-mini', provider: 'openai', max_input_tokens: 128000,
      input_cost_per_1m: 0.15, output_cost_per_1m: 0.6, position: 5, registered: true },
    { name: 'zeta-chat', provider: 'zeta', max_input_tokens: null,
      input_cost_per_1m: null, output_cost_per_1m: null, position: 1, registered: false },
  ],
};

/** Routed fetch mock: serves the catalog GET; delegates everything else
 * (the mutation under test) to `mutationResponse`. */
function routedFetch(mutationResponse: (req: Request) => Promise<Response>) {
  return vi.fn(async (req: Request) => {
    if (req.url.includes('/admin/models/catalog')) {
      return new Response(JSON.stringify(fixtureCatalog), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    return mutationResponse(req);
  });
}

const created = (status = 201) => async (_req: Request) =>
  new Response(JSON.stringify({ id: 'm1', key_fingerprint: 'ab12…ef90' }), {
    status,
    headers: { 'content-type': 'application/json' },
  });

function mutationCalls(fetchMock: ReturnType<typeof vi.fn>): Request[] {
  return fetchMock.mock.calls
    .map((c) => c[0] as Request)
    .filter((r) => !r.url.includes('/admin/models/catalog'));
}

function renderDialog(
  fetchMock = routedFetch(created()),
  options: {
    model?: ModelOut | null;
    onOpenChange?: (open: boolean) => void;
    queryClient?: QueryClient;
  } = {},
) {
  vi.stubGlobal('fetch', fetchMock);
  const queryClient =
    options.queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

async function openProviderList(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByLabelText('Provider'));
  return within(await screen.findByRole('listbox'));
}

test('provider picker lists catalog providers pinned-first, then the custom entry', async () => {
  const user = userEvent.setup();
  renderDialog();
  const list = await openProviderList(user);
  const options = (await list.findAllByRole('option')).map((o) => o.textContent);
  // openai/anthropic/gemini are pinned (in that order); zeta is unpinned and
  // follows alphabetically; the manual path always closes the list.
  expect(options).toEqual(['openai', 'anthropic', 'gemini', 'zeta', 'Custom / self-hosted']);
});

test('provider picker filters as you type', async () => {
  const user = userEvent.setup();
  renderDialog();
  await openProviderList(user);
  await user.type(screen.getByLabelText('Provider'), 'anthro');
  const options = within(screen.getByRole('listbox'))
    .getAllByRole('option')
    .map((o) => o.textContent);
  expect(options).toEqual(['anthropic']);
});

test('model picker shows the provider models newest-first with context and pricing', async () => {
  const user = userEvent.setup();
  renderDialog();
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'anthropic' }));
  await user.click(screen.getByLabelText('Model'));
  const options = within(screen.getByRole('listbox')).getAllByRole('option');
  // position DESC: claude-4-sonnet (9) before claude-3-haiku (2).
  expect(options[0]).toHaveTextContent('claude-4-sonnet');
  expect(options[0]).toHaveTextContent('200k');
  expect(options[0]).toHaveTextContent('$3.00/$15.00');
  expect(options[1]).toHaveTextContent('claude-3-haiku');
  expect(options).toHaveLength(2);
});

test('selecting a model fills the verbatim name, suggests a display name, and derives litellm kind', async () => {
  const fetchMock = routedFetch(created());
  const user = userEvent.setup();
  renderDialog(fetchMock);
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'gemini' }));
  await user.click(screen.getByLabelText('Model'));
  await user.click(screen.getByRole('option', { name: /gemini\/gemini-2\.5-pro/ }));
  // Suggested display name is prettified and editable.
  expect(screen.getByLabelText('Display name')).toHaveValue('Gemini 2.5 Pro');
  await user.type(screen.getByLabelText('API key'), 'AIza-test-1');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
  const req = mutationCalls(fetchMock)[0]!;
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toMatchObject({
    display_name: 'Gemini 2.5 Pro',
    litellm_model_name: 'gemini/gemini-2.5-pro', // verbatim catalog name
    provider_kind: 'litellm', // derived, never shown to the user
    api_key: 'AIza-test-1',
  });
  expect(body).not.toHaveProperty('base_url');
});

test('catalog openai provider derives provider_kind openai', async () => {
  const fetchMock = routedFetch(created());
  const user = userEvent.setup();
  renderDialog(fetchMock);
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'openai' }));
  await user.click(screen.getByLabelText('Model'));
  await user.click(screen.getByRole('option', { name: /gpt-4o-mini/ }));
  await user.type(screen.getByLabelText('API key'), 'sk-test-123');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
  const body = JSON.parse(await mutationCalls(fetchMock)[0]!.clone().text()) as Record<
    string,
    unknown
  >;
  expect(body).toMatchObject({
    litellm_model_name: 'gpt-4o-mini',
    provider_kind: 'openai',
    display_name: 'Gpt 4o Mini',
  });
});

test('custom path reveals the manual fields: kind select, model id, base URL for self-hosted', async () => {
  const user = userEvent.setup();
  renderDialog();
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'Custom / self-hosted' }));
  expect(screen.getByLabelText('Model id')).toBeInTheDocument();
  const kindSelect = screen.getByLabelText('Provider kind');
  const kinds = within(kindSelect)
    .getAllByRole('option')
    .map((o) => (o as HTMLOptionElement).value);
  expect(kinds).toEqual(['openai', 'ollama', 'openai_compatible']);
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument(); // openai default
  await user.selectOptions(kindSelect, 'ollama');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
  expect(screen.queryByLabelText('API key')).not.toBeInTheDocument(); // ollama is keyless
  await user.selectOptions(kindSelect, 'openai_compatible');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
  const key = screen.getByLabelText('API key');
  expect(key).toHaveAttribute('type', 'password');
  expect(key).toHaveAttribute('autocomplete', 'off');
});

test('custom path submits the assembled payload', async () => {
  const fetchMock = routedFetch(created());
  const user = userEvent.setup();
  renderDialog(fetchMock);
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'Custom / self-hosted' }));
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('API key'), 'sk-test-123');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
  const body = JSON.parse(await mutationCalls(fetchMock)[0]!.clone().text()) as Record<
    string,
    unknown
  >;
  expect(body).toMatchObject({
    display_name: 'GPT-4o mini',
    litellm_model_name: 'gpt-4o-mini',
    provider_kind: 'openai',
    api_key: 'sk-test-123',
  });
});

test('checking "unreliable at native tool calling" includes tools_unreliable in the create payload', async () => {
  const fetchMock = routedFetch(created());
  const user = userEvent.setup();
  renderDialog(fetchMock);
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'Custom / self-hosted' }));
  await user.type(screen.getByLabelText('Model id'), 'gemma4:e4b');
  await user.type(screen.getByLabelText('Display name'), 'Gemma');
  const checkbox = screen.getByLabelText('Unreliable at native tool calling');
  expect(checkbox).not.toBeChecked();
  await user.click(checkbox);
  expect(checkbox).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
  const body = JSON.parse(await mutationCalls(fetchMock)[0]!.clone().text()) as Record<
    string,
    unknown
  >;
  expect(body).toMatchObject({ tools_unreliable: true });
});

test('editing a flagged model shows the checkbox pre-checked and unchecking it PATCHes false', async () => {
  const fetchMock = routedFetch(created(200));
  const user = userEvent.setup();
  renderDialog(fetchMock, { model: { ...fixtureModel, tools_unreliable: true } });
  const checkbox = screen.getByLabelText('Unreliable at native tool calling');
  expect(checkbox).toBeChecked();
  await user.click(checkbox);
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  await vi.waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
  const body = JSON.parse(await mutationCalls(fetchMock)[0]!.clone().text()) as Record<
    string,
    unknown
  >;
  expect(body).toMatchObject({ tools_unreliable: false });
});

test('a 502 still invalidates the caches and closes the dialog as a partial success', async () => {
  const fetchMock = routedFetch(async (_req: Request) =>
    new Response(JSON.stringify({ detail: 'gateway unreachable' }), {
      status: 502,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const user = userEvent.setup();
  const { onOpenChange } = renderDialog(fetchMock, { queryClient });
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'Custom / self-hosted' }));
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  // The local write succeeded (only the gateway sync failed) — the dialog closes.
  await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['admin-models'] });
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['models'] });
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('gateway sync failed'));
});

test('a non-502 failure keeps the dialog open with a distinguishable generic message, but still invalidates', async () => {
  const fetchMock = routedFetch(async (_req: Request) =>
    new Response(JSON.stringify({ detail: 'boom' }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const user = userEvent.setup();
  const { onOpenChange } = renderDialog(fetchMock, { queryClient });
  const providers = await openProviderList(user);
  await user.click(providers.getByRole('option', { name: 'Custom / self-hosted' }));
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('request failed');
  expect(onOpenChange).not.toHaveBeenCalled();
  await vi.waitFor(() =>
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['admin-models'] }),
  );
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['models'] });
});

test('edit mode: provider and model id are read-only, API key starts blank with a fingerprint hint', () => {
  const fetchMock = routedFetch(created(200));
  renderDialog(fetchMock, { model: fixtureModel });
  expect(screen.getByLabelText('Provider')).toBeDisabled();
  expect(screen.getByLabelText('Model id')).toBeDisabled();
  const key = screen.getByLabelText('API key') as HTMLInputElement;
  expect(key.value).toBe('');
  expect(key.getAttribute('placeholder')).toContain('ab12…ef90');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
  // Edit mode never needs the catalog — no fetch at all.
  expect(fetchMock).not.toHaveBeenCalled();
});

test('edit mode PATCHes only the fields that changed', async () => {
  const fetchMock = routedFetch(created(200));
  const user = userEvent.setup();
  renderDialog(fetchMock, { model: fixtureModel });
  await user.clear(screen.getByLabelText('Display name'));
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini v2');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  await vi.waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
  const req = mutationCalls(fetchMock)[0]!;
  expect(req.method).toBe('PATCH');
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toEqual({ display_name: 'GPT-4o mini v2' });
});

test('deriveProviderKind maps openai and ollama through, everything else to litellm', () => {
  expect(deriveProviderKind('openai')).toBe('openai');
  expect(deriveProviderKind('ollama')).toBe('ollama');
  expect(deriveProviderKind('anthropic')).toBe('litellm');
  expect(deriveProviderKind('gemini')).toBe('litellm');
});

test('prettifyModelName: last path segment, dashes to spaces, title case', () => {
  expect(prettifyModelName('gemini/gemini-2.5-pro')).toBe('Gemini 2.5 Pro');
  expect(prettifyModelName('gpt-4o-mini')).toBe('Gpt 4o Mini');
  expect(prettifyModelName('claude-4-sonnet')).toBe('Claude 4 Sonnet');
});
