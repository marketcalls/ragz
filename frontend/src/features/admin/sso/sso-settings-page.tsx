import { Check } from 'lucide-react';
import { useEffect, useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';

import {
  useAdminOrgs,
  usePutOrgSsoDomains,
  usePutSsoConfig,
  useSsoConfig,
  type AdminOrg,
} from './queries';

function ConfiguredBadge() {
  return (
    <span className="inline-flex items-center gap-1 text-[12px] text-success">
      <Check className="h-3 w-3" aria-hidden />
      set
    </span>
  );
}

// Comma-separated free text -> a clean domain list: trim, lowercase, drop
// empties, dedupe (preserving first-seen order). Applied on save so the stored
// value never carries whitespace/case/duplicate noise.
function parseDomains(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(',')) {
    const d = part.trim().toLowerCase();
    if (d && !seen.has(d)) {
      seen.add(d);
      out.push(d);
    }
  }
  return out;
}

function OrgDomainsRow({ org }: { org: AdminOrg }) {
  const put = usePutOrgSsoDomains();
  const [domains, setDomains] = useState((org.sso_domains ?? []).join(', '));

  // Re-sync when the org query refetches (e.g. after this row's own save).
  useEffect(() => {
    setDomains((org.sso_domains ?? []).join(', '));
  }, [org.sso_domains]);

  function onSave() {
    put.mutate({ orgId: org.id, domains: parseDomains(domains) });
  }

  return (
    <div className="space-y-2 rounded-md border border-line bg-raised p-4">
      <Label htmlFor={`org-domains-${org.id}`}>{org.name}</Label>
      <div className="flex items-start gap-3">
        <Input
          id={`org-domains-${org.id}`}
          value={domains}
          onChange={(e) => setDomains(e.target.value)}
          placeholder="example.com, corp.example.com"
        />
        <Button variant="secondary" onClick={onSave} disabled={put.isPending}>
          {put.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
      {put.isError ? (
        <p role="alert" className="text-[12px] text-danger">
          {put.error.message}
        </p>
      ) : null}
      {put.isSuccess ? <p className="text-[12px] text-success">Saved.</p> : null}
    </div>
  );
}

export function SsoSettingsPage() {
  const config = useSsoConfig();
  const orgs = useAdminOrgs();
  const update = usePutSsoConfig();

  const [issuer, setIssuer] = useState('');
  const [clientId, setClientId] = useState('');
  // Secret field is write-only: never populated from the query response,
  // always starts blank, and is only sent in the PUT body when the user
  // actually typed something (mirrors email-settings-page.tsx).
  const [clientSecret, setClientSecret] = useState('');

  useEffect(() => {
    if (config.data) {
      setIssuer(config.data.issuer ?? '');
      setClientId(config.data.client_id ?? '');
    }
  }, [config.data]);

  // Only clear the secret field once the PUT actually succeeds -- a failed
  // save keeps the just-typed value instead of silently discarding it.
  useEffect(() => {
    if (update.isSuccess) {
      setClientSecret('');
    }
  }, [update.isSuccess]);

  function onSave() {
    update.mutate({
      issuer,
      client_id: clientId,
      ...(clientSecret ? { client_secret: clientSecret } : {}),
    });
  }

  return (
    <>
      <TopBar title="SSO" />
      <div className="flex-1 overflow-y-auto p-6">
        {config.isPending ? <Spinner label="Loading SSO settings…" /> : null}
        {config.isError ? (
          <QueryError error={config.error} onRetry={() => config.refetch()} />
        ) : null}
        {config.data ? (
          <div className="mx-auto max-w-xl space-y-8">
            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Single sign-on (OIDC)</h3>
              <div>
                <Label htmlFor="sso-issuer">Issuer</Label>
                <Input
                  id="sso-issuer"
                  value={issuer}
                  onChange={(e) => setIssuer(e.target.value)}
                  placeholder="https://accounts.google.com"
                />
                <p className="mt-1 text-[12px] text-muted">
                  For Google/Gmail, use <code>https://accounts.google.com</code>.
                </p>
              </div>
              <div>
                <Label htmlFor="sso-client-id">Client ID</Label>
                <Input
                  id="sso-client-id"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <Label htmlFor="sso-client-secret" className="mb-0">
                    Client secret
                  </Label>
                  {config.data.client_secret_set ? <ConfiguredBadge /> : null}
                </div>
                <Input
                  id="sso-client-secret"
                  type="password"
                  autoComplete="off"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  placeholder={config.data.client_secret_set ? '••••••••' : ''}
                />
                <p className="mt-1 text-[12px] text-muted">
                  {config.data.client_secret_set
                    ? '(set — leave blank to keep)'
                    : 'Leave blank to keep the current value.'}
                </p>
              </div>

              {update.isError ? <QueryError error={update.error} onRetry={onSave} /> : null}
              <div className="flex items-center gap-3">
                <Button variant="primary" onClick={onSave} disabled={update.isPending}>
                  {update.isPending ? 'Saving…' : 'Save'}
                </Button>
                {update.isSuccess ? <span className="text-sm text-success">Saved.</span> : null}
              </div>
              <p className="text-[12px] text-muted">
                New users are auto-provisioned on first SSO login only if their email domain is
                allowed below, and always with the &lsquo;user&rsquo; role.
              </p>
            </section>

            <section className="space-y-3 border-t border-line pt-6">
              <h3 className="text-sm font-semibold text-ink">Allowed email domains</h3>
              <p className="text-[12px] text-muted">
                A first-time SSO login self-provisions a new user only when their email domain is
                listed for exactly one org.
              </p>
              {orgs.isPending ? <Spinner label="Loading organizations…" /> : null}
              {orgs.isError ? (
                <QueryError error={orgs.error} onRetry={() => orgs.refetch()} />
              ) : null}
              {orgs.data ? (
                <div className="space-y-3">
                  {orgs.data.map((org) => (
                    <OrgDomainsRow key={org.id} org={org} />
                  ))}
                </div>
              ) : null}
            </section>
          </div>
        ) : null}
      </div>
    </>
  );
}
