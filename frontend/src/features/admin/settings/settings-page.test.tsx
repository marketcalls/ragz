import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ProviderSettings } from './queries';

const useProviderSettings = vi.fn();
const useUpdateProviderSettings = vi.fn();
vi.mock('./queries', () => ({
  useProviderSettings: () => useProviderSettings(),
  useUpdateProviderSettings: () => useUpdateProviderSettings(),
}));

// The Embedding section feeds its dropdown from the model registry and renders
// the registry table itself. Both reach TanStack Query, which this suite has no
// provider for — stub them so these tests stay about the settings form.
const useAdminModels = vi.fn();
vi.mock('../models/queries', () => ({
  useAdminModels: () => useAdminModels(),
}));
vi.mock('../models/registered-models-table', () => ({
  RegisteredModelsTable: ({ modality }: { modality: string }) => (
    <div data-testid={`registry-${modality}`} />
  ),
}));

import { SettingsPage } from './settings-page';

const settings: ProviderSettings = {
  document_parser: 'docling',
  rerank_provider: 'local',
  cohere_rerank_model: 'rerank-v4.0-fast',
  web_search_provider: 'duckduckgo',
  web_search_full_content: true,
  default_chunk_method: 'heading',
  generative_ui_images: 'off',
  generative_ui_enabled: true,
  llamaparse_key_set: false,
  cohere_key_set: false,
  tavily_key_set: false,
  default_embedding_model_id: null,
};

const putSpy = vi.fn();

beforeEach(() => {
  useAdminModels.mockReturnValue({ data: [], isPending: false, isError: false });
  useProviderSettings.mockReturnValue({
    data: settings,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  useUpdateProviderSettings.mockReturnValue({
    mutate: putSpy,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: false,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders current settings and masks keys', async () => {
  render(<SettingsPage />);

  expect(await screen.findByLabelText(/document parser/i)).toHaveValue('docling');
  expect(screen.getByLabelText(/reranker/i)).toHaveValue('local');
  expect(screen.getByLabelText(/llamaparse api key/i)).toHaveAttribute('type', 'password');
  expect(screen.getByLabelText(/cohere api key/i)).toHaveAttribute('type', 'password');
});

test('picking Cohere reveals the rerank model select', async () => {
  render(<SettingsPage />);

  expect(screen.queryByLabelText(/cohere model/i)).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText(/reranker/i), 'cohere');
  expect(screen.getByLabelText(/cohere model/i)).toBeInTheDocument();
});

test('submitting sends a PUT with the new reranker and key', async () => {
  render(<SettingsPage />);

  await userEvent.selectOptions(screen.getByLabelText(/reranker/i), 'cohere');
  await userEvent.type(screen.getByLabelText(/cohere api key/i), 'ck-live');
  await userEvent.click(screen.getByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledWith(
    expect.objectContaining({ rerank_provider: 'cohere', cohere_api_key: 'ck-live' }),
  );
});

test('saving with the default Local reranker omits cohere_rerank_model from the PUT body', async () => {
  render(<SettingsPage />);

  await userEvent.click(screen.getByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledTimes(1);
  const body = putSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.rerank_provider).toBe('local');
  expect(body.cohere_rerank_model).toBeUndefined();
});

test('leaving a key field blank on save omits it from the PUT body', async () => {
  render(<SettingsPage />);

  await userEvent.selectOptions(screen.getByLabelText(/reranker/i), 'cohere');
  // Cohere is selected but no key is typed — the field stays blank.
  await userEvent.click(screen.getByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledTimes(1);
  const body = putSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.cohere_api_key).toBeUndefined();
  expect(body.llamaparse_api_key).toBeUndefined();
});

test('renders the web-search provider select defaulting to DuckDuckGo', async () => {
  render(<SettingsPage />);

  expect(await screen.findByLabelText(/web search provider/i)).toHaveValue('duckduckgo');
  // Tavily key field is hidden until Tavily is selected.
  expect(screen.queryByLabelText(/tavily api key/i)).not.toBeInTheDocument();
});

test('picking Tavily reveals the write-only API key field', async () => {
  render(<SettingsPage />);

  await userEvent.selectOptions(screen.getByLabelText(/web search provider/i), 'tavily');
  const key = screen.getByLabelText(/tavily api key/i);
  expect(key).toBeInTheDocument();
  expect(key).toHaveAttribute('type', 'password');
});

test('sends the tavily key only when typed, omitting it when blank', async () => {
  const { unmount } = render(<SettingsPage />);

  // Blank: Tavily selected, no key typed -> key omitted.
  await userEvent.selectOptions(screen.getByLabelText(/web search provider/i), 'tavily');
  await userEvent.click(screen.getByRole('button', { name: /save/i }));
  let body = putSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.web_search_provider).toBe('tavily');
  expect(body.tavily_api_key).toBeUndefined();

  unmount();
  putSpy.mockClear();

  // Typed: key is included.
  render(<SettingsPage />);
  await userEvent.selectOptions(screen.getByLabelText(/web search provider/i), 'tavily');
  await userEvent.type(screen.getByLabelText(/tavily api key/i), 'tvly-live');
  await userEvent.click(screen.getByRole('button', { name: /save/i }));
  body = putSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.tavily_api_key).toBe('tvly-live');
});

test('sends the default chunking strategy on save', async () => {
  render(<SettingsPage />);

  await userEvent.selectOptions(screen.getByLabelText(/default chunking strategy/i), 'page');
  await userEvent.click(screen.getByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledWith(
    expect.objectContaining({ default_chunk_method: 'page' }),
  );
});

test('offers anydoc as a parser option and selects it when reported by the backend', async () => {
  useProviderSettings.mockReturnValue({
    data: { ...settings, document_parser: 'anydoc' },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });

  render(<SettingsPage />);

  expect(await screen.findByRole('option', { name: /anydoc/i })).toBeInTheDocument();
  expect(screen.getByLabelText(/document parser/i)).toHaveValue('anydoc');
});

test('offers liteparse (the recommended default) as a parser option', async () => {
  useProviderSettings.mockReturnValue({
    data: { ...settings, document_parser: 'liteparse' },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });

  render(<SettingsPage />);

  expect(await screen.findByRole('option', { name: /liteparse/i })).toBeInTheDocument();
  expect(screen.getByLabelText(/document parser/i)).toHaveValue('liteparse');
});

test('renders the generative UI images select defaulting to Off', async () => {
  render(<SettingsPage />);

  expect(await screen.findByLabelText(/generative ui images/i)).toHaveValue('off');
});

test('changing generative UI images to web results and saving sends it in the PUT body', async () => {
  render(<SettingsPage />);

  await userEvent.selectOptions(
    screen.getByLabelText(/generative ui images/i),
    'web_results',
  );
  await userEvent.click(screen.getByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledWith(
    expect.objectContaining({ generative_ui_images: 'web_results' }),
  );
});

test('renders the rich generative UI checkbox from the mocked value (on)', async () => {
  render(<SettingsPage />);

  expect(await screen.findByLabelText(/rich generative ui/i)).toBeChecked();
});

test('unchecking rich generative UI and saving sends generative_ui_enabled false', async () => {
  render(<SettingsPage />);

  await userEvent.click(await screen.findByLabelText(/rich generative ui/i));
  await userEvent.click(screen.getByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledWith(
    expect.objectContaining({ generative_ui_enabled: false }),
  );
});

test('saving unchanged includes generative_ui_enabled true in the PUT body', async () => {
  render(<SettingsPage />);

  await userEvent.click(await screen.findByRole('button', { name: /save/i }));

  expect(putSpy).toHaveBeenCalledWith(
    expect.objectContaining({ generative_ui_enabled: true }),
  );
});

test('shows an error message and retry button when the settings query fails', async () => {
  const refetch = vi.fn();
  useProviderSettings.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load settings'),
    refetch,
  });

  render(<SettingsPage />);

  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});

test('the default embedding dropdown offers local and OpenAI embedders only', () => {
  useAdminModels.mockReturnValue({
    data: [
      { id: 'e1', display_name: 'Local Embeddings (bge-m3)', provider_kind: 'tei',
        modality: 'embedding', dimension: 1024 },
      { id: 'e2', display_name: 'text-embedding-3-large', provider_kind: 'openai',
        modality: 'embedding', dimension: 3072 },
      // Neither of these may be offered as the install-wide default.
      { id: 'e3', display_name: 'Cohere Embed', provider_kind: 'cohere',
        modality: 'embedding', dimension: 1024 },
      { id: 'c1', display_name: 'GPT-4o', provider_kind: 'openai',
        modality: 'chat', dimension: null },
    ],
    isPending: false,
    isError: false,
  });
  render(<SettingsPage />);
  const select = screen.getByLabelText('Default embedding model');
  const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
  // Dimensions are surfaced because a mismatched one only shows up as a failed
  // ingestion much later.
  expect(labels).toEqual([
    'Not set — new workspaces use the built-in local model',
    'Local Embeddings (bge-m3) — 1024',
    'text-embedding-3-large — 3072',
  ]);
});

test('saving sends the chosen default embedding model', async () => {
  const user = userEvent.setup();
  useAdminModels.mockReturnValue({
    data: [
      { id: 'e2', display_name: 'text-embedding-3-large', provider_kind: 'openai',
        modality: 'embedding', dimension: 3072 },
    ],
    isPending: false,
    isError: false,
  });
  render(<SettingsPage />);
  await user.selectOptions(screen.getByLabelText('Default embedding model'), 'e2');
  await user.click(screen.getByRole('button', { name: /save/i }));
  expect(putSpy).toHaveBeenCalledWith(
    expect.objectContaining({ default_embedding_model_id: 'e2' }),
  );
});

test('an unset default is omitted from the payload rather than sent empty', async () => {
  const user = userEvent.setup();
  render(<SettingsPage />);
  await user.click(screen.getByRole('button', { name: /save/i }));
  expect(putSpy).toHaveBeenCalledWith(
    expect.not.objectContaining({ default_embedding_model_id: expect.anything() }),
  );
});
