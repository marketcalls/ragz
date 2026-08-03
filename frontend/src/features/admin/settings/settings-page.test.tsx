import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ProviderSettings } from './queries';

const useProviderSettings = vi.fn();
const useUpdateProviderSettings = vi.fn();
vi.mock('./queries', () => ({
  useProviderSettings: () => useProviderSettings(),
  useUpdateProviderSettings: () => useUpdateProviderSettings(),
}));

import { SettingsPage } from './settings-page';

const settings: ProviderSettings = {
  document_parser: 'docling',
  rerank_provider: 'local',
  cohere_rerank_model: 'rerank-v4.0-fast',
  llamaparse_key_set: false,
  cohere_key_set: false,
};

const putSpy = vi.fn();

beforeEach(() => {
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
