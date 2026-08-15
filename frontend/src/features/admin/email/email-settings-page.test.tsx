import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { EmailConfig } from './queries';

const useEmailConfig = vi.fn();
const useUpdateEmailConfig = vi.fn();
const useSendTestEmail = vi.fn();
vi.mock('./queries', () => ({
  useEmailConfig: () => useEmailConfig(),
  useUpdateEmailConfig: () => useUpdateEmailConfig(),
  useSendTestEmail: () => useSendTestEmail(),
}));

import { EmailSettingsPage } from './email-settings-page';

const smtpConfig: EmailConfig = {
  provider: 'smtp',
  from_email: 'noreply@example.com',
  from_name: 'Ragz',
  smtp_host: 'smtp.example.com',
  smtp_port: 587,
  smtp_use_tls: true,
  smtp_username: 'mailer',
  ses_region: '',
  ses_access_key_id: '',
  smtp_password_set: false,
  ses_secret_key_set: false,
};

const putSpy = vi.fn();
const testSpy = vi.fn();

beforeEach(() => {
  useEmailConfig.mockReturnValue({
    data: smtpConfig,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  useUpdateEmailConfig.mockReturnValue({
    mutate: putSpy,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: false,
  });
  useSendTestEmail.mockReturnValue({
    mutate: testSpy,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: false,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

test('renders current settings with the SMTP group visible and secret fields blank', async () => {
  render(<EmailSettingsPage />);

  expect(await screen.findByLabelText(/from email/i)).toHaveValue('noreply@example.com');
  expect(screen.getByLabelText(/^host$/i)).toHaveValue('smtp.example.com');
  expect(screen.getByLabelText(/^password$/i)).toHaveAttribute('type', 'password');
  expect(screen.getByLabelText(/^password$/i)).toHaveValue('');
  expect(screen.queryByLabelText(/region/i)).not.toBeInTheDocument();
});

test('shows a "configured" indicator when a secret is already set, none when it is not', async () => {
  render(<EmailSettingsPage />);
  expect(screen.queryByText(/configured/i)).not.toBeInTheDocument();

  useEmailConfig.mockReturnValue({
    data: { ...smtpConfig, smtp_password_set: true },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  render(<EmailSettingsPage />);
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
  // The secret itself is never rendered -- the field stays blank even
  // though the backend reports one is set, only the existence indicator shows.
  expect(screen.getByLabelText(/^password$/i)).toHaveValue('');
});

test('toggling the provider switches the visible field group', async () => {
  const user = userEvent.setup();
  render(<EmailSettingsPage />);

  expect(screen.getByLabelText(/^host$/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/region/i)).not.toBeInTheDocument();

  await user.click(screen.getByRole('radio', { name: /amazon ses/i }));

  expect(screen.queryByLabelText(/^host$/i)).not.toBeInTheDocument();
  expect(screen.getByLabelText(/region/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/access key id/i)).toBeInTheDocument();
});

test('saving with a blank secret field omits it from the PUT body', async () => {
  const user = userEvent.setup();
  render(<EmailSettingsPage />);

  await user.click(screen.getByRole('button', { name: /^save$/i }));

  expect(putSpy).toHaveBeenCalledTimes(1);
  const body = putSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.smtp_password).toBeUndefined();
  expect(body.ses_secret_key).toBeUndefined();
  expect(body).toMatchObject({
    provider: 'smtp',
    from_email: 'noreply@example.com',
    smtp_host: 'smtp.example.com',
  });
});

test('typing a secret includes it in the PUT body', async () => {
  const user = userEvent.setup();
  render(<EmailSettingsPage />);

  await user.type(screen.getByLabelText(/^password$/i), 'hunter2');
  await user.click(screen.getByRole('button', { name: /^save$/i }));

  expect(putSpy).toHaveBeenCalledTimes(1);
  const body = putSpy.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(body.smtp_password).toBe('hunter2');
});

test('sending a test email fires the mutation with the entered address', async () => {
  const user = userEvent.setup();
  render(<EmailSettingsPage />);

  await user.type(screen.getByLabelText(/recipient/i), 'me@example.com');
  await user.click(screen.getByRole('button', { name: /send test email/i }));

  expect(testSpy).toHaveBeenCalledTimes(1);
  expect(testSpy).toHaveBeenCalledWith('me@example.com');
});

test('shows an error message and retry button when the settings query fails', async () => {
  const refetch = vi.fn();
  useEmailConfig.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: true,
    error: new Error('failed to load email settings'),
    refetch,
  });

  render(<EmailSettingsPage />);

  expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i);
  await userEvent.click(screen.getByRole('button', { name: /retry/i }));
  expect(refetch).toHaveBeenCalledTimes(1);
});
