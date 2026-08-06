import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ApiKeyCreatedOut, ApiKeyOut } from '@/api/types';

const useApiKeys = vi.fn();
const useCreateApiKey = vi.fn();
const useRevokeApiKey = vi.fn();
vi.mock('./queries', () => ({
  useApiKeys: () => useApiKeys(),
  useCreateApiKey: () => useCreateApiKey(),
  useRevokeApiKey: () => useRevokeApiKey(),
}));

const useUsers = vi.fn();
vi.mock('@/features/admin/users/queries', () => ({
  useUsers: () => useUsers(),
}));

const useWorkspaces = vi.fn();
vi.mock('@/features/workspaces/queries', () => ({
  useWorkspaces: () => useWorkspaces(),
}));

import { ApiKeysPage } from './api-keys-page';

const keyA: ApiKeyOut = {
  id: 'k1',
  name: 'CI bot',
  prefix: 'ragz_sk_ab12',
  org_id: 'o1',
  user_id: 'u1',
  workspace_id: 'w1',
  created_by: 'u1',
  expires_at: null,
  last_used_at: null,
  revoked_at: null,
  created_at: '2026-07-01T00:00:00Z',
};

const created: ApiKeyCreatedOut = {
  ...keyA,
  id: 'k2',
  name: 'New key',
  prefix: 'ragz_sk_faketestkey',
  api_key: 'ragz_sk_faketestkey_RAW_SECRET_VALUE',
};

const createMutate = vi.fn();
const revokeMutate = vi.fn();

beforeEach(() => {
  useApiKeys.mockReturnValue({
    data: [keyA],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  useCreateApiKey.mockReturnValue({
    mutate: createMutate,
    data: undefined,
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  });
  useRevokeApiKey.mockReturnValue({
    mutate: revokeMutate,
    isPending: false,
  });
  useUsers.mockReturnValue({ data: [{ id: 'u1', email: 'alice@acme.com', role: 'user', active: true }] });
  useWorkspaces.mockReturnValue({ data: [{ id: 'w1', name: 'Default' }] });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders the table with masked rows: prefix shown, no raw key anywhere', () => {
  render(<ApiKeysPage />);

  expect(screen.getByText('CI bot')).toBeInTheDocument();
  expect(screen.getByText('ragz_sk_ab12')).toBeInTheDocument();
  expect(screen.queryByText(/RAW_SECRET_VALUE/)).not.toBeInTheDocument();
});

test('opening Generate and submitting calls POST, then shows the raw key exactly once in a copy field', async () => {
  const user = userEvent.setup();
  const { rerender } = render(<ApiKeysPage />);

  await user.click(screen.getByRole('button', { name: /generate key/i }));
  await user.type(screen.getByLabelText(/name/i), 'New key');
  await user.selectOptions(screen.getByLabelText(/user/i), 'u1');
  await user.selectOptions(screen.getByLabelText(/workspace/i), 'w1');
  await user.click(screen.getByRole('button', { name: /^generate$/i }));

  expect(createMutate).toHaveBeenCalledTimes(1);
  const [body] = createMutate.mock.calls[0] as [Record<string, unknown>, unknown];
  expect(body).toMatchObject({ name: 'New key', user_id: 'u1', workspace_id: 'w1' });

  // Simulate the mutation having resolved with the created key (the dialog
  // stays open — same instance, so its local state survives the rerender).
  useCreateApiKey.mockReturnValue({
    mutate: createMutate,
    data: created,
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  });
  rerender(<ApiKeysPage />);

  // Restrict to the <input>: the dialog's own aria-labelledby (pointing at
  // its "Generate API key" title) also matches /api key/i on the dialog div.
  const keyField = screen.getByLabelText(/api key/i, { selector: 'input' });
  expect(keyField).toHaveValue(created.api_key);
  // Exactly once: the copy field's own display value, nowhere else in the DOM.
  expect(screen.getAllByDisplayValue(created.api_key)).toHaveLength(1);
  expect(screen.getByText(/copy it now/i)).toBeInTheDocument();

  // The raw key must never appear as rendered text anywhere else on the page
  // (e.g. leaked into a table cell) -- only inside that one readonly input's
  // value, which getByText/queryByText do not match.
  expect(screen.queryAllByText(created.api_key)).toHaveLength(0);
});

test('clicking Revoke opens a confirm dialog naming the key; confirming calls DELETE', async () => {
  const user = userEvent.setup();
  render(<ApiKeysPage />);

  await user.click(screen.getByRole('button', { name: /revoke ci bot/i }));
  expect(revokeMutate).not.toHaveBeenCalled();
  // The confirm dialog names the key and warns of immediate breakage.
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText(/ci bot/i)).toBeInTheDocument();
  expect(dialog.getByText(/stop working immediately/i)).toBeInTheDocument();

  // The row's own button is named "Revoke CI bot" (via aria-label); the
  // dialog's danger confirm button is the only one accessibly named exactly
  // "Revoke", so this can't accidentally re-click the row button.
  await user.click(screen.getByRole('button', { name: 'Revoke' }));

  expect(revokeMutate).toHaveBeenCalledTimes(1);
  expect(revokeMutate).toHaveBeenCalledWith('k1', expect.anything());
});

test('clicking Revoke then Cancel does not call DELETE', async () => {
  const user = userEvent.setup();
  render(<ApiKeysPage />);

  await user.click(screen.getByRole('button', { name: /revoke ci bot/i }));
  await user.click(screen.getByRole('button', { name: /cancel/i }));

  expect(revokeMutate).not.toHaveBeenCalled();
});

test('shows an error message and retry button when the list query fails', async () => {
  const refetch = vi.fn();
  useApiKeys.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load API keys'),
    refetch,
  });

  render(<ApiKeysPage />);

  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
