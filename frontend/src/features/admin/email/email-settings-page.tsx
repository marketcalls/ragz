import { Check } from 'lucide-react';
import { useEffect, useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';

import { useEmailConfig, useSendTestEmail, useUpdateEmailConfig, type EmailProvider } from './queries';

function ConfiguredBadge() {
  return (
    <span className="inline-flex items-center gap-1 text-[12px] text-success">
      <Check className="h-3 w-3" aria-hidden />
      configured
    </span>
  );
}

export function EmailSettingsPage() {
  const config = useEmailConfig();
  const update = useUpdateEmailConfig();
  const sendTest = useSendTestEmail();

  const [provider, setProvider] = useState<EmailProvider>('smtp');
  const [fromEmail, setFromEmail] = useState('');
  const [fromName, setFromName] = useState('');
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpUseTls, setSmtpUseTls] = useState(true);
  const [smtpUsername, setSmtpUsername] = useState('');
  const [sesRegion, setSesRegion] = useState('');
  const [sesAccessKeyId, setSesAccessKeyId] = useState('');
  // Secret fields are write-only: never populated from the query response,
  // always start blank, and are only sent in the PUT body when the user
  // actually typed something (mirrors settings-page.tsx's llamaKey/cohereKey).
  const [smtpPassword, setSmtpPassword] = useState('');
  const [sesSecretKey, setSesSecretKey] = useState('');

  const [testTo, setTestTo] = useState('');

  useEffect(() => {
    if (config.data) {
      setProvider(config.data.provider);
      setFromEmail(config.data.from_email);
      setFromName(config.data.from_name);
      setSmtpHost(config.data.smtp_host);
      setSmtpPort(config.data.smtp_port);
      setSmtpUseTls(config.data.smtp_use_tls);
      setSmtpUsername(config.data.smtp_username);
      setSesRegion(config.data.ses_region);
      setSesAccessKeyId(config.data.ses_access_key_id);
    }
  }, [config.data]);

  // Only clear the secret fields once the PUT actually succeeds -- a failed
  // save keeps the just-typed value instead of silently discarding it.
  useEffect(() => {
    if (update.isSuccess) {
      setSmtpPassword('');
      setSesSecretKey('');
    }
  }, [update.isSuccess]);

  function onSave() {
    update.mutate({
      provider,
      from_email: fromEmail,
      from_name: fromName,
      smtp_host: smtpHost,
      smtp_port: smtpPort,
      smtp_use_tls: smtpUseTls,
      smtp_username: smtpUsername,
      ses_region: sesRegion,
      ses_access_key_id: sesAccessKeyId,
      ...(smtpPassword ? { smtp_password: smtpPassword } : {}),
      ...(sesSecretKey ? { ses_secret_key: sesSecretKey } : {}),
    });
  }

  function onSendTest() {
    sendTest.mutate(testTo);
  }

  return (
    <>
      <TopBar title="Email" />
      <div className="flex-1 overflow-y-auto p-6">
        {config.isPending ? <Spinner label="Loading email settings…" /> : null}
        {config.isError ? (
          <QueryError error={config.error} onRetry={() => config.refetch()} />
        ) : null}
        {config.data ? (
          <div className="mx-auto max-w-xl space-y-8">
            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Provider</h3>
              <div role="radiogroup" aria-label="Email provider" className="flex gap-2">
                <Button
                  type="button"
                  role="radio"
                  aria-checked={provider === 'smtp'}
                  variant={provider === 'smtp' ? 'primary' : 'secondary'}
                  onClick={() => setProvider('smtp')}
                >
                  SMTP
                </Button>
                <Button
                  type="button"
                  role="radio"
                  aria-checked={provider === 'ses'}
                  variant={provider === 'ses' ? 'primary' : 'secondary'}
                  onClick={() => setProvider('ses')}
                >
                  Amazon SES
                </Button>
              </div>
            </section>

            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Sender</h3>
              <div>
                <Label htmlFor="from-email">From email</Label>
                <Input
                  id="from-email"
                  type="email"
                  value={fromEmail}
                  onChange={(e) => setFromEmail(e.target.value)}
                  placeholder="noreply@example.com"
                />
              </div>
              <div>
                <Label htmlFor="from-name">From name</Label>
                <Input
                  id="from-name"
                  value={fromName}
                  onChange={(e) => setFromName(e.target.value)}
                  placeholder="Ragz"
                />
              </div>
            </section>

            {provider === 'smtp' ? (
              <section className="space-y-3">
                <h3 className="text-sm font-semibold text-ink">SMTP</h3>
                <div>
                  <Label htmlFor="smtp-host">Host</Label>
                  <Input
                    id="smtp-host"
                    value={smtpHost}
                    onChange={(e) => setSmtpHost(e.target.value)}
                    placeholder="smtp.example.com"
                  />
                </div>
                <div>
                  <Label htmlFor="smtp-port">Port</Label>
                  <Input
                    id="smtp-port"
                    type="number"
                    value={smtpPort}
                    onChange={(e) => setSmtpPort(Number(e.target.value))}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <input
                    id="smtp-use-tls"
                    type="checkbox"
                    checked={smtpUseTls}
                    onChange={(e) => setSmtpUseTls(e.target.checked)}
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                  <Label htmlFor="smtp-use-tls" className="mb-0">
                    Use TLS
                  </Label>
                </div>
                <div>
                  <Label htmlFor="smtp-username">Username</Label>
                  <Input
                    id="smtp-username"
                    value={smtpUsername}
                    onChange={(e) => setSmtpUsername(e.target.value)}
                  />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <Label htmlFor="smtp-password" className="mb-0">
                      Password
                    </Label>
                    {config.data.smtp_password_set ? <ConfiguredBadge /> : null}
                  </div>
                  <Input
                    id="smtp-password"
                    type="password"
                    autoComplete="off"
                    value={smtpPassword}
                    onChange={(e) => setSmtpPassword(e.target.value)}
                    placeholder={config.data.smtp_password_set ? '••••••••' : ''}
                  />
                  <p className="mt-1 text-[12px] text-muted">
                    Leave blank to keep the current value.
                  </p>
                </div>
              </section>
            ) : (
              <section className="space-y-3">
                <h3 className="text-sm font-semibold text-ink">Amazon SES</h3>
                <div>
                  <Label htmlFor="ses-region">Region</Label>
                  <Input
                    id="ses-region"
                    value={sesRegion}
                    onChange={(e) => setSesRegion(e.target.value)}
                    placeholder="us-east-1"
                  />
                </div>
                <div>
                  <Label htmlFor="ses-access-key">Access key ID</Label>
                  <Input
                    id="ses-access-key"
                    value={sesAccessKeyId}
                    onChange={(e) => setSesAccessKeyId(e.target.value)}
                  />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <Label htmlFor="ses-secret-key" className="mb-0">
                      Secret key
                    </Label>
                    {config.data.ses_secret_key_set ? <ConfiguredBadge /> : null}
                  </div>
                  <Input
                    id="ses-secret-key"
                    type="password"
                    autoComplete="off"
                    value={sesSecretKey}
                    onChange={(e) => setSesSecretKey(e.target.value)}
                    placeholder={config.data.ses_secret_key_set ? '••••••••' : ''}
                  />
                  <p className="mt-1 text-[12px] text-muted">
                    Leave blank to keep the current value.
                  </p>
                </div>
              </section>
            )}

            {update.isError ? <QueryError error={update.error} onRetry={onSave} /> : null}
            <div className="flex items-center gap-3">
              <Button variant="primary" onClick={onSave} disabled={update.isPending}>
                {update.isPending ? 'Saving…' : 'Save'}
              </Button>
              {update.isSuccess ? <span className="text-sm text-success">Saved.</span> : null}
            </div>

            <section className="space-y-3 border-t border-line pt-6">
              <h3 className="text-sm font-semibold text-ink">Send test email</h3>
              <div>
                <Label htmlFor="test-to">Recipient</Label>
                <Input
                  id="test-to"
                  type="email"
                  value={testTo}
                  onChange={(e) => setTestTo(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              {sendTest.isError ? (
                <p role="alert" className="text-[12px] text-danger">
                  {sendTest.error.message}
                </p>
              ) : null}
              {sendTest.isSuccess ? (
                <p className="text-[12px] text-success">Test email sent.</p>
              ) : null}
              <Button onClick={onSendTest} disabled={sendTest.isPending || !testTo}>
                {sendTest.isPending ? 'Sending…' : 'Send test email'}
              </Button>
            </section>
          </div>
        ) : null}
      </div>
    </>
  );
}
