import { Download } from 'lucide-react';
import { useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { downloadCsv, toCsv } from './csv';
import { useAuditLog, type AuditFilters } from './queries';

const COLUMNS = ['created_at', 'action', 'actor_id', 'org_id', 'target_type', 'target_id'];

export function AuditPage() {
  const [draft, setDraft] = useState<AuditFilters>({});
  const [filters, setFilters] = useState<AuditFilters>({});
  const log = useAuditLog(filters);
  const events = log.data?.pages.flatMap((p) => p.events) ?? [];

  const exportCsv = () =>
    downloadCsv(
      `raghub-audit-${new Date().toISOString().slice(0, 10)}.csv`,
      toCsv(
        events.map((e) => ({
          created_at: e.created_at, action: e.action, actor_id: e.actor_id,
          org_id: e.org_id, target_type: e.target_type, target_id: e.target_id,
        })),
        COLUMNS,
      ),
    );

  return (
    <>
      <TopBar
        title="Audit log"
        actions={
          <Button size="sm" onClick={exportCsv} disabled={events.length === 0}>
            <Download className="h-3.5 w-3.5" aria-hidden /> Export CSV ({events.length})
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <form
          className="mb-3 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setFilters(draft);
          }}
        >
          <Input aria-label="Action prefix" placeholder="action (e.g. login)"
            className="w-40" value={draft.action ?? ''}
            onChange={(e) => setDraft({ ...draft, action: e.target.value || undefined })} />
          <Input aria-label="Actor id" placeholder="actor uuid" className="w-64"
            value={draft.actor_id ?? ''}
            onChange={(e) => setDraft({ ...draft, actor_id: e.target.value || undefined })} />
          <Input aria-label="Org id" placeholder="org uuid" className="w-64"
            value={draft.org_id ?? ''}
            onChange={(e) => setDraft({ ...draft, org_id: e.target.value || undefined })} />
          <Input aria-label="From" type="date" value={draft.date_from ?? ''}
            onChange={(e) => setDraft({ ...draft, date_from: e.target.value || undefined })} />
          <Input aria-label="To" type="date" value={draft.date_to ?? ''}
            onChange={(e) => setDraft({ ...draft, date_to: e.target.value || undefined })} />
          <Button type="submit" size="sm">Apply</Button>
        </form>
        {log.isPending ? <Spinner label="Loading audit log…" /> : null}
        {log.isError ? <QueryError error={log.error} onRetry={() => log.refetch()} /> : null}
        {log.data ? (
          <Table>
            <THead>
              <TR>{COLUMNS.map((c) => <TH key={c}>{c}</TH>)}</TR>
            </THead>
            <TBody>
              {events.map((e) => (
                <TR key={e.id}>
                  <TD className="whitespace-nowrap">{new Date(e.created_at).toLocaleString()}</TD>
                  <TD className="font-medium">{e.action}</TD>
                  <TD className="font-mono text-[11px]">{e.actor_id ?? '—'}</TD>
                  <TD className="font-mono text-[11px]">{e.org_id ?? '—'}</TD>
                  <TD>{e.target_type}</TD>
                  <TD className="font-mono text-[11px]">{e.target_id}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        ) : null}
        {log.hasNextPage ? (
          <Button className="mt-3" onClick={() => void log.fetchNextPage()}
            disabled={log.isFetchingNextPage}>
            {log.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        ) : null}
      </div>
    </>
  );
}
