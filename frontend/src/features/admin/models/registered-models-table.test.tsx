import { render, screen } from '@testing-library/react';

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
  // The table always mounts ModelFormDialog (closed by default); it calls
  // useCreateModel/useCatalog unconditionally regardless of open state.
  useCreateModel: () => useCreateModel(),
}));

import { RegisteredModelsTable } from './registered-models-table';

const chatModel: ModelOut = {
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

const builtinEmbedder: ModelOut = {
  ...chatModel,
  id: 'm3',
  litellm_model_name: 'BAAI/bge-m3',
  display_name: 'TEI embeddings',
  provider_kind: 'tei',
  is_utility: false,
  modality: 'embedding',
  dimension: 1024,
  collection_name: 'ws_default_bge_m3',
};

const hostedEmbedder: ModelOut = {
  ...builtinEmbedder,
  id: 'm4',
  litellm_model_name: 'text-embedding-3-large',
  display_name: 'OpenAI Large',
  provider_kind: 'openai',
  dimension: 3072,
  collection_name: 'chunks_openai_large',
};

beforeEach(() => {
  useAdminModels.mockReturnValue({
    data: [chatModel, builtinEmbedder, hostedEmbedder],
    isPending: false,
  });
  useCatalog.mockReturnValue({ data: { entries: [], new_available: 0 } });
  usePatchModel.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useDeleteModel.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useCreateModel.mockReturnValue({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders only rows of the requested modality', () => {
  render(<RegisteredModelsTable modality="embedding" />);
  expect(screen.getByText('TEI embeddings')).toBeInTheDocument();
  expect(screen.getByText('OpenAI Large')).toBeInTheDocument();
  expect(screen.queryByText('GPT-4o mini')).not.toBeInTheDocument();
});

test('the built-in tei row is not editable or removable', () => {
  render(<RegisteredModelsTable modality="embedding" />);
  expect(screen.queryByLabelText('Edit TEI embeddings')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Remove TEI embeddings')).not.toBeInTheDocument();
  expect(screen.getByText('Built-in')).toBeInTheDocument();
  // A registered hosted embedder stays fully manageable.
  expect(screen.getByLabelText('Edit OpenAI Large')).toBeInTheDocument();
  expect(screen.getByLabelText('Remove OpenAI Large')).toBeInTheDocument();
});

test('the Dimension column appears for embedding models only', () => {
  const { unmount } = render(<RegisteredModelsTable modality="embedding" />);
  expect(screen.getByRole('columnheader', { name: 'Dimension' })).toBeInTheDocument();
  expect(screen.getByText('3072')).toBeInTheDocument();
  unmount();

  render(<RegisteredModelsTable modality="chat" />);
  expect(screen.queryByRole('columnheader', { name: 'Dimension' })).not.toBeInTheDocument();
});

test('the add button is opt-in', () => {
  const { unmount } = render(<RegisteredModelsTable modality="embedding" />);
  expect(screen.queryByRole('button', { name: /add embedding model/i })).not.toBeInTheDocument();
  unmount();

  render(<RegisteredModelsTable modality="embedding" showAdd />);
  expect(screen.getByRole('button', { name: /add embedding model/i })).toBeInTheDocument();
});
