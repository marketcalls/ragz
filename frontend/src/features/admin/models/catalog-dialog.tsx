import { Check, Copy } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toaster';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { useCatalog } from './queries';

// Read-only browse: copying a name does not register it. One-click enable
// straight from this list is MODEL-10's later slice (Task 13 brief) — the
// existing manual "Add model" form stays the only way to register.
export function CatalogDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const catalog = useCatalog(open);
  const [filter, setFilter] = useState('');
  const [copiedName, setCopiedName] = useState<string | null>(null);

  const entries = useMemo(() => {
    const all = catalog.data?.entries ?? [];
    const query = filter.trim().toLowerCase();
    return query ? all.filter((e) => e.name.toLowerCase().includes(query)) : all;
  }, [catalog.data, filter]);

  const copyName = (name: string): void => {
    navigator.clipboard
      .writeText(name)
      .then(() => {
        setCopiedName(name);
        toast('Copied to clipboard');
        setTimeout(() => setCopiedName((cur) => (cur === name ? null : cur)), 1500);
      })
      .catch(() => toast('Copy failed'));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Model catalog"
        description="LiteLLM's known models. Copy a name into Add model to register it — this list is read-only."
        className="max-w-2xl"
      >
        <Input
          aria-label="Filter catalog"
          placeholder="Filter by name…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {catalog.isPending ? <Spinner label="Loading catalog…" /> : null}
        {catalog.data ? (
          <Table className="mt-3">
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Provider</TH>
                <TH>Context</TH>
                <TH>Pricing</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {entries.map((entry) => (
                <TR key={entry.name}>
                  <TD className="font-mono text-[12px]">
                    {entry.name}
                    {entry.registered ? (
                      <StatusPill tone="success" className="ml-2">
                        Registered
                      </StatusPill>
                    ) : null}
                  </TD>
                  <TD className="text-secondary">{entry.provider}</TD>
                  <TD className="tabular-nums text-secondary">
                    {entry.max_input_tokens ? `${Math.round(entry.max_input_tokens / 1000)}k` : '—'}
                  </TD>
                  <TD className="tabular-nums text-secondary">
                    {entry.input_cost_per_1m != null
                      ? `$${entry.input_cost_per_1m.toFixed(2)} / $${(
                          entry.output_cost_per_1m ?? 0
                        ).toFixed(2)}`
                      : '—'}
                  </TD>
                  <TD className="text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Copy ${entry.name}`}
                      onClick={() => copyName(entry.name)}
                    >
                      {copiedName === entry.name ? (
                        <Check className="h-3.5 w-3.5" aria-hidden />
                      ) : (
                        <Copy className="h-3.5 w-3.5" aria-hidden />
                      )}
                    </Button>
                  </TD>
                </TR>
              ))}
              {entries.length === 0 ? (
                <TR>
                  <TD colSpan={5} className="text-center text-muted">
                    No matches.
                  </TD>
                </TR>
              ) : null}
            </TBody>
          </Table>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
