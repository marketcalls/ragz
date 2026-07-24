import { render, screen } from '@testing-library/react';
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

beforeEach(() => {
  useAdminModels.mockReturnValue({ data: [modelA, modelB, modelC], isPending: false });
  useCatalog.mockReturnValue({ data: { entries: [], new_available: 0 } });
  usePatchModel.mockReturnValue({ mutate: patchMutate, isPending: false });
  useDeleteModel.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useCreateModel.mockReturnValue({ mutate: vi.fn(), isPending: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('the designated utility model renders its radio checked, the other unchecked', () => {
  render(<ModelsPage />);
  const aRadio = screen.getByLabelText('Use GPT-4o mini as the utility model');
  const bRadio = screen.getByLabelText('Use Llama 3 as the utility model');
  expect(aRadio).toBeChecked();
  expect(bRadio).not.toBeChecked();
});

test('clicking another row\'s radio PATCHes only that row with is_utility: true', async () => {
  const user = userEvent.setup();
  render(<ModelsPage />);
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
  render(<ModelsPage />);
  expect(
    screen.getByText(/powers answer-quality scoring, evals, and \(later\) enrichment/),
  ).toBeInTheDocument();
});

test('the chat tab is selected by default and shows only chat-modality rows', () => {
  render(<ModelsPage />);
  expect(screen.getByText('GPT-4o mini')).toBeInTheDocument();
  expect(screen.getByText('Llama 3')).toBeInTheDocument();
  expect(screen.queryByText('TEI embeddings')).not.toBeInTheDocument();
});

test('switching to the embedding tab shows only embedding-modality rows, with the built-in tei row unremovable', async () => {
  const user = userEvent.setup();
  render(<ModelsPage />);
  await user.click(screen.getByRole('button', { name: 'embedding models' }));
  expect(screen.queryByText('GPT-4o mini')).not.toBeInTheDocument();
  expect(screen.queryByText('Llama 3')).not.toBeInTheDocument();
  expect(screen.getByText('TEI embeddings')).toBeInTheDocument();
  expect(screen.queryByLabelText('Edit TEI embeddings')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Remove TEI embeddings')).not.toBeInTheDocument();
  expect(screen.getByText('Built-in')).toBeInTheDocument();
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
  render(<ModelsPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
