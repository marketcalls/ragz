import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';

import type { ModelOut } from '@/api/types';

const useAdminModels = vi.fn();
const useCatalog = vi.fn();
const usePatchModel = vi.fn();
const useDeleteModel = vi.fn();
const useCreateModel = vi.fn();
vi.mock('./queries', () => ({
  useAdminModels: () => useAdminModels(),
  useCatalog: (...args: unknown[]) => useCatalog(...args),
  usePatchModel: () => usePatchModel(),
  useDeleteModel: () => useDeleteModel(),
  // ModelsPage always mounts ModelFormDialog (closed by default); it calls
  // useCreateModel/useCatalog unconditionally regardless of open state.
  useCreateModel: () => useCreateModel(),
}));

import { ModelsPage } from './models-page';

/** ModelsPage renders a <Link> to Settings > Embedding, so it needs a router. */
const renderPage = () => render(<ModelsPage />, { wrapper: MemoryRouter });

const modelA: ModelOut = {
  id: 'm1',
  litellm_model_name: 'gpt-4o-mini',
  display_name: 'GPT-4o mini',
  provider_kind: 'openai',
  base_url: null,
  enabled: true,
  key_fingerprint: null,
  sync_status: 'synced',
  mock_response: null,
  tools_unreliable: false,
  is_utility: true,
  supports_reasoning: false,
  default_reasoning_effort: 'off',
  supports_vision: false,
  modality: 'chat',
  dimension: null,
  collection_name: null,
};

const modelB: ModelOut = {
  id: 'm2',
  litellm_model_name: 'llama3',
  display_name: 'Llama 3',
  provider_kind: 'ollama',
  base_url: 'http://ollama:11434',
  enabled: true,
  key_fingerprint: null,
  sync_status: 'synced',
  mock_response: null,
  tools_unreliable: false,
  is_utility: false,
  supports_reasoning: false,
  default_reasoning_effort: 'off',
  supports_vision: false,
  modality: 'chat',
  dimension: null,
  collection_name: null,
};

const modelC: ModelOut = {
  id: 'm3',
  litellm_model_name: 'BAAI/bge-m3',
  display_name: 'TEI embeddings',
  provider_kind: 'tei',
  base_url: null,
  enabled: true,
  key_fingerprint: null,
  sync_status: 'synced',
  mock_response: null,
  tools_unreliable: false,
  is_utility: false,
  supports_reasoning: false,
  default_reasoning_effort: 'off',
  supports_vision: false,
  modality: 'embedding',
  dimension: 1024,
  collection_name: 'ws_default_bge_m3',
};

const patchMutate = vi.fn();
const createSpy = vi.fn();

beforeEach(() => {
  useAdminModels.mockReturnValue({ data: [modelA, modelB, modelC], isPending: false });
  useCatalog.mockReturnValue({ data: { entries: [], new_available: 0 } });
  usePatchModel.mockReturnValue({ mutate: patchMutate, isPending: false });
  useDeleteModel.mockReturnValue({ mutate: vi.fn(), isPending: false });
  createSpy.mockResolvedValue(undefined);
  useCreateModel.mockReturnValue({ mutate: vi.fn(), mutateAsync: createSpy, isPending: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('the designated utility model renders its radio checked, the other unchecked', () => {
  renderPage();
  const aRadio = screen.getByLabelText('Use GPT-4o mini as the utility model');
  const bRadio = screen.getByLabelText('Use Llama 3 as the utility model');
  expect(aRadio).toBeChecked();
  expect(bRadio).not.toBeChecked();
});

test('clicking another row\'s radio PATCHes only that row with is_utility: true', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.click(screen.getByLabelText('Use Llama 3 as the utility model'));
  expect(patchMutate).toHaveBeenCalledTimes(1);
  // Exactly { modelId: 'm2', body: { is_utility: true } } -- no `false` body
  // is ever sent for any other row; exclusivity is the backend's job.
  expect(patchMutate).toHaveBeenCalledWith(
    { modelId: 'm2', body: { is_utility: true } },
    expect.anything(),
  );
});

test('a short caption explains the utility model designation', () => {
  renderPage();
  expect(
    screen.getByText(/powers answer-quality scoring, evals, and \(later\) enrichment/),
  ).toBeInTheDocument();
});

test('the chat tab is selected by default and shows only chat-modality rows', () => {
  renderPage();
  expect(screen.getByText('GPT-4o mini')).toBeInTheDocument();
  expect(screen.getByText('Llama 3')).toBeInTheDocument();
  expect(screen.queryByText('TEI embeddings')).not.toBeInTheDocument();
});

test('lists only chat rows, and points at Settings for the embedding registry', () => {
  // The chat/embedding tab switch is gone: embedding models moved to
  // Settings > Embedding, next to the global default that selects from them.
  renderPage();
  expect(screen.getByText('GPT-4o mini')).toBeInTheDocument();
  expect(screen.queryByText('TEI embeddings')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'embedding models' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Settings . Embedding/ })).toHaveAttribute(
    'href',
    '/admin/settings',
  );
});

test('shows an error message and retry button when the query fails', async () => {
  const refetch = vi.fn();
  useAdminModels.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load models'),
    refetch,
  });
  renderPage();
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});

test('the Type column displays the modality of each model', () => {
  renderPage();
  expect(screen.getAllByText('chat', { selector: 'td' }).length).toBeGreaterThan(0);
  // The embedding half of this now lives in registered-models-table.test.tsx.
  expect(screen.queryByText('embedding', { selector: 'td' })).not.toBeInTheDocument();
});

test('renders a searchable provider-card grid (e.g. an Anthropic card)', () => {
  renderPage();
  expect(screen.getByRole('button', { name: /anthropic/i })).toBeInTheDocument();
});

test('typing in the provider search narrows the grid', async () => {
  const user = userEvent.setup();
  renderPage();
  expect(screen.getByText('Cohere')).toBeInTheDocument();
  await user.type(screen.getByLabelText('Search providers'), 'anthropic');
  expect(screen.queryByText('Cohere')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /anthropic/i })).toBeInTheDocument();
});

test('registers a suggested model from a provider card with a prefilled body', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.click(screen.getByRole('button', { name: /anthropic/i }));
  const claudeCheckboxes = await screen.findAllByRole('checkbox', { name: /claude/i });
  await user.click(claudeCheckboxes[0]!);
  await user.click(screen.getByRole('button', { name: /add selected/i }));
  expect(createSpy).toHaveBeenCalled();
  const body = createSpy.mock.calls[0]![0];
  expect(body.provider_kind).toBe('litellm');
  expect(body.litellm_model_name).toMatch(/^anthropic\//);
  expect(body.modality).toBe('chat');
});

test('registering a suggested embedding model sends modality and the entered dimension', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.click(screen.getByRole('button', { name: /jina/i }));
  const jinaCheckboxes = await screen.findAllByRole('checkbox', { name: /jina-embeddings/i });
  await user.click(jinaCheckboxes[0]!);
  await user.type(
    screen.getByLabelText(/embedding dimension/i),
    '1024',
  );
  await user.click(screen.getByRole('button', { name: /add selected/i }));
  expect(createSpy).toHaveBeenCalled();
  const body = createSpy.mock.calls[0]![0];
  expect(body.modality).toBe('embedding');
  expect(body.dimension).toBe(1024);
});

test('checking a suggested embedding model without a dimension blocks submission', async () => {
  const user = userEvent.setup();
  renderPage();
  await user.click(screen.getByRole('button', { name: /jina/i }));
  const jinaCheckboxes = await screen.findAllByRole('checkbox', { name: /jina-embeddings/i });
  await user.click(jinaCheckboxes[0]!);
  await user.click(screen.getByRole('button', { name: /add selected/i }));
  expect(createSpy).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/dimension/i);
});

test('a suggested model already registered renders checked and disabled', async () => {
  const anthropicModel: ModelOut = {
    ...modelA,
    id: 'm4',
    provider_kind: 'litellm',
    litellm_model_name: 'anthropic/claude-opus-4-8',
    display_name: 'Opus',
  };
  useAdminModels.mockReturnValue({ data: [modelA, modelB, modelC, anthropicModel], isPending: false });
  const user = userEvent.setup();
  renderPage();
  await user.click(screen.getByRole('button', { name: /anthropic/i }));
  const registeredCheckbox = await screen.findByRole('checkbox', { name: /claude-opus-4-8/i });
  expect(registeredCheckbox).toBeChecked();
  expect(registeredCheckbox).toBeDisabled();
});
