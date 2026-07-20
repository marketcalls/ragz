import { useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { StatTile } from '@/components/ui/stat-tile';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { OrgQuotaDialog } from '../quotas/org-quota-dialog';

import { useClientErrors, useSystemHealth, type OrgsHealth, type OrgUsageRow } from './queries';

// Pill carries the status word in visible text (never color-only) — a11y
// rule from the brief.
function HealthPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${
        ok ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger'
      }`}
    >
      {label}: {ok ? 'Healthy' : 'Failed'}
    </span>
  );
}

const MESSAGE_TRUNCATE = 120;

function truncate(message: string): string {
  return message.length > MESSAGE_TRUNCATE
    ? `${message.slice(0, MESSAGE_TRUNCATE)}…`
    : message;
}

function isOrgsError(orgs: OrgsHealth): orgs is { status: 'error'; detail?: string } {
  return !Array.isArray(orgs);
}

export function HealthPage() {
  const health = useSystemHealth();
  const clientErrors = useClientErrors();
  const data = health.data;
  // Task 15: superadmin org-quota editor, mounted from this existing per-org
  // table — there is no standalone org list/settings page yet.
  const [quotaOrg, setQuotaOrg] = useState<OrgUsageRow | null>(null);

  return (
    <>
      <TopBar title="System health" />
      <div className="flex-1 overflow-y-auto p-6">
        {health.isPending ? <Spinner label="Loading system health…" /> : null}
        {health.isError ? (
          <QueryError error={health.error} onRetry={() => health.refetch()} />
        ) : null}
        {data ? (
          <div className="mx-auto max-w-5xl space-y-6">
            <div className="flex flex-wrap gap-2">
              <HealthPill label="LiteLLM" ok={data.litellm.status === 'ok'} />
              <HealthPill label="Qdrant" ok={data.qdrant.status === 'ok'} />
              <HealthPill label="Queues" ok={data.queues.status === 'ok'} />
            </div>

            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {data.queues.depths
                ? Object.entries(data.queues.depths).map(([queue, depth]) => (
                    <StatTile key={queue} label={`${queue} queue depth`} value={String(depth)} />
                  ))
                : null}
            </div>

            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-ink">Qdrant collections</h3>
              {data.qdrant.collections && data.qdrant.collections.length > 0 ? (
                <Table>
                  <THead>
                    <TR>
                      <TH>Collection</TH>
                      <TH className="text-right">Points</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {data.qdrant.collections.map((c) => (
                      <TR key={c.name}>
                        <TD>{c.name}</TD>
                        <TD className="text-right">{c.points_count.toLocaleString()}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              ) : (
                <p className="text-xs text-secondary">No collection data available.</p>
              )}
            </div>

            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-ink">Per-org usage (30 days)</h3>
              {isOrgsError(data.orgs) ? (
                <p className="text-xs text-danger">Org usage rollup unavailable.</p>
              ) : (
                <Table>
                  <THead>
                    <TR>
                      <TH>Org</TH>
                      <TH className="text-right">Tokens</TH>
                      <TH />
                    </TR>
                  </THead>
                  <TBody>
                    {data.orgs.map((o) => (
                      <TR key={o.org_id}>
                        <TD>{o.name}</TD>
                        <TD className="text-right">{o.tokens.toLocaleString()}</TD>
                        <TD className="text-right">
                          <Button size="sm" onClick={() => setQuotaOrg(o)}>
                            Manage quota
                          </Button>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </div>

            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-ink">Recent client errors</h3>
              {/* message/stack/url are attacker-controlled — rendered as plain
                  JSX text below, never dangerouslySetInnerHTML. */}
              <Table>
                <THead>
                  <TR>
                    <TH>Time</TH>
                    <TH>User</TH>
                    <TH>Message</TH>
                    <TH>URL</TH>
                  </TR>
                </THead>
                <TBody>
                  {(clientErrors.data ?? []).map((e, i) => (
                    <TR key={`${e.ts}-${i}`}>
                      <TD className="whitespace-nowrap">
                        {new Date(e.ts * 1000).toLocaleString()}
                      </TD>
                      <TD className="font-mono text-[11px]">{e.user_id}</TD>
                      <TD>{truncate(e.message)}</TD>
                      <TD className="font-mono text-[11px]">{e.url ?? '—'}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>
        ) : null}
      </div>
      {quotaOrg ? (
        <OrgQuotaDialog
          open
          onOpenChange={(o) => !o && setQuotaOrg(null)}
          orgId={quotaOrg.org_id}
          orgName={quotaOrg.name}
          usageTokens={quotaOrg.tokens}
        />
      ) : null}
    </>
  );
}
