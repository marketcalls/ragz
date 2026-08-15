import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { BotIntegrationOut } from '@/api/types';

const useBots = vi.fn();
const useCreateBot = vi.fn();
const useSetBotEnabled = vi.fn();
const useDeleteBot = vi.fn();
vi.mock('./queries', () => ({
  useBots: () => useBots(),
  useCreateBot: () => useCreateBot(),
  useSetBotEnabled: () => useSetBotEnabled(),
  useDeleteBot: () => useDeleteBot(),
}));

const useUsers = vi.fn();
vi.mock('@/features/admin/users/queries', () => ({
  useUsers: () => useUsers(),
}));

const useWorkspaces = vi.fn();
vi.mock('@/features/workspaces/queries', () => ({
  useWorkspaces: () => useWorkspaces(),
}));

import { BotsPage } from './bots-page';

const botA: BotIntegrationOut = {
  id: 'b1',
  platform: 'telegram',
  name: 'Support bot',
  org_id: 'o1',
  workspace_id: 'w1',
  user_id: 'u1',
  webhook_id: 'wh1',
  webhook_url: 'https://ragz.example.com/external/bots/telegram/wh1',
  enabled: true,
  created_by: 'u1',
  created_at: '2026-07-01T00:00:00Z',
};

const created: BotIntegrationOut = {
  ...botA,
  id: 'b2',
  name: 'New bot',
  platform: 'discord',
  webhook_id: 'wh2',
  webhook_url: 'https://ragz.example.com/external/bots/discord/wh2',
};

const createMutate = vi.fn();
const setEnabledMutate = vi.fn();
const deleteMutate = vi.fn();

beforeEach(() => {
  useBots.mockReturnValue({
    data: [botA],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  useCreateBot.mockReturnValue({
    mutate: createMutate,
    data: undefined,
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  });
  useSetBotEnabled.mockReturnValue({
    mutate: setEnabledMutate,
    isPending: false,
  });
  useDeleteBot.mockReturnValue({
    mutate: deleteMutate,
    isPending: false,
  });
  useUsers.mockReturnValue({
    data: [{ id: 'u1', email: 'alice@acme.com', role: 'user', active: true }],
  });
  useWorkspaces.mockReturnValue({ data: [{ id: 'w1', name: 'Default' }] });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders the table with masked rows: no token/signing_secret anywhere', () => {
  render(<BotsPage />);

  expect(screen.getByText('Support bot')).toBeInTheDocument();
  expect(within(screen.getByRole('table')).getByText(/telegram/i)).toBeInTheDocument();
  expect(screen.getByText('Default')).toBeInTheDocument();
  expect(screen.queryByText(/token/i, { selector: 'td' })).not.toBeInTheDocument();
  expect(screen.queryByText(/signing_secret/i)).not.toBeInTheDocument();
});

test('shows a platform card per active platform with a connected-count badge, plus a disabled WhatsApp coming-soon card', async () => {
  const user = userEvent.setup();
  render(<BotsPage />);

  const telegramCard = screen.getByRole('button', { name: /telegram/i });
  expect(within(telegramCard).getByText('1 connected')).toBeInTheDocument();

  const discordCard = screen.getByRole('button', { name: /discord/i });
  expect(within(discordCard).getByText('0 connected')).toBeInTheDocument();

  const slackCard = screen.getByRole('button', { name: /slack/i });
  expect(within(slackCard).getByText('0 connected')).toBeInTheDocument();

  const whatsappCard = screen.getByRole('button', { name: /whatsapp/i });
  expect(whatsappCard).toBeDisabled();
  expect(whatsappCard).toHaveAttribute('aria-disabled', 'true');
  expect(within(whatsappCard).getByText(/coming soon/i)).toBeInTheDocument();

  await user.click(whatsappCard);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('clicking a platform card opens the add-bot dialog pre-set to that platform; submitting calls POST; on success shows webhook_url in a copy field, never the credentials', async () => {
  const user = userEvent.setup();
  const { rerender } = render(<BotsPage />);

  await user.click(screen.getByRole('button', { name: /discord/i }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText(/platform/i)).toHaveValue('Discord');

  await user.type(within(dialog).getByLabelText(/name/i), 'New bot');
  await user.selectOptions(within(dialog).getByLabelText(/user/i), 'u1');
  await user.selectOptions(within(dialog).getByLabelText(/workspace/i), 'w1');
  await user.type(within(dialog).getByLabelText(/token/i), 'super-secret-token');
  await user.type(
    within(dialog).getByLabelText(/signing secret/i),
    'super-secret-signing-secret',
  );
  await user.click(within(dialog).getByRole('button', { name: /^add bot$/i }));

  expect(createMutate).toHaveBeenCalledTimes(1);
  const [body] = createMutate.mock.calls[0] as [Record<string, unknown>, unknown];
  expect(body).toMatchObject({
    platform: 'discord',
    name: 'New bot',
    user_id: 'u1',
    workspace_id: 'w1',
    token: 'super-secret-token',
    signing_secret: 'super-secret-signing-secret',
  });

  // Simulate the mutation having resolved with the created integration (the
  // dialog stays open -- same instance, so its local state survives the
  // rerender -- mirroring api-keys-page.test.tsx).
  useCreateBot.mockReturnValue({
    mutate: createMutate,
    data: created,
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  });
  rerender(<BotsPage />);

  const urlField = screen.getByLabelText(/webhook url/i, { selector: 'input' });
  expect(urlField).toHaveValue(created.webhook_url);

  // Credentials never rendered anywhere in the dialog/page.
  expect(screen.queryByDisplayValue('super-secret-token')).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue('super-secret-signing-secret')).not.toBeInTheDocument();
  expect(screen.queryAllByText(/super-secret/)).toHaveLength(0);
});

test('the table never displays credentials', () => {
  render(<BotsPage />);

  const table = screen.getByRole('table');
  expect(within(table).queryByText(/super-secret/i)).not.toBeInTheDocument();
});

test('clicking the copy button copies webhook_url from the row', async () => {
  const user = userEvent.setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });

  render(<BotsPage />);

  await user.click(screen.getByRole('button', { name: /copy webhook url/i }));

  expect(writeText).toHaveBeenCalledWith(botA.webhook_url);
});

test('toggling the enabled switch calls PATCH', async () => {
  const user = userEvent.setup();
  render(<BotsPage />);

  await user.click(screen.getByRole('checkbox', { name: /support bot/i }));

  expect(setEnabledMutate).toHaveBeenCalledTimes(1);
  expect(setEnabledMutate).toHaveBeenCalledWith(
    { id: 'b1', enabled: false },
    expect.anything(),
  );
});

test('clicking Remove opens a confirm dialog; confirming calls DELETE, Cancel does not', async () => {
  const user = userEvent.setup();
  render(<BotsPage />);

  await user.click(screen.getByRole('button', { name: /remove support bot/i }));
  expect(deleteMutate).not.toHaveBeenCalled();

  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText(/support bot/i)).toBeInTheDocument();

  await user.click(dialog.getByRole('button', { name: /cancel/i }));
  expect(deleteMutate).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: /remove support bot/i }));
  await user.click(screen.getByRole('button', { name: 'Remove' }));

  expect(deleteMutate).toHaveBeenCalledTimes(1);
  expect(deleteMutate).toHaveBeenCalledWith('b1', expect.anything());
});
