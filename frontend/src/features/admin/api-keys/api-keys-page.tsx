import { Plus } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';

import type { ApiKeyOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill, type StatusTone } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';
import { useUsers } from '@/features/admin/users/queries';
import { useWorkspaces } from '@/features/workspaces/queries';

import { useApiKeys, useCreateApiKey, useRevokeApiKey } from './queries';

type Status = 'active' | 'expired' | 'revoked';

function statusOf(key: ApiKeyOut): Status {
  if (key.revoked_at) return 'revoked';
  if (key.expires_at && new Date(key.expires_at).getTime() <= Date.now()) return 'expired';
  return 'active';
}

function statusTone(status: Status): StatusTone {
  if (status === 'active') return 'success';
  if (status === 'expired') return 'muted';
  return 'danger';
}

export function ApiKeysPage() {
  const keys = useApiKeys();
  const users = useUsers();
  const workspaces = useWorkspaces();
  const createKey = useCreateApiKey();
  const revokeKey = useRevokeApiKey();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [userId, setUserId] = useState('');
  const [workspaceId, setWorkspaceId] = useState('');
  const [expiresAt, setExpiresAt] = useState('');

  const userEmailById = useMemo(
    () => new Map((users.data ?? []).map((u): [string, string] => [u.id, u.email])),
    [users.data],
  );
  const workspaceNameById = useMemo(
    () => new Map((workspaces.data ?? []).map((w): [string, string] => [w.id, w.name])),
    [workspaces.data],
  );

  const close = (next: boolean): void => {
    if (!next) {
      setName('');
      setUserId('');
      setWorkspaceId('');
      setExpiresAt('');
      createKey.reset();
    }
    setOpen(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    createKey.mutate({
      name,
      user_id: userId,
      workspace_id: workspaceId,
      ...(expiresAt ? { expires_at: new Date(expiresAt).toISOString() } : {}),
    });
  };

  const onRevoke = (key: ApiKeyOut): void => {
    revokeKey.mutate(key.id, { onError: (err) => toast.error(err.message) });
  };

  return (
    <>
      <TopBar
        title="API Keys"
        actions={
          <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Generate key
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          {keys.isPending ? <Spinner label="Loading API keys…" /> : null}
          {keys.isError ? (
            <QueryError error={keys.error} onRetry={() => keys.refetch()} />
          ) : null}
          {keys.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Key</TH>
                  <TH>Workspace</TH>
                  <TH>User</TH>
                  <TH>Last used</TH>
                  <TH>Status</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {keys.data.map((key) => {
                  const status = statusOf(key);
                  return (
                    <TR key={key.id}>
                      <TD className="font-medium">{key.name}</TD>
                      <TD className="font-mono text-[12px] text-secondary">{key.prefix}</TD>
                      <TD className="text-secondary">
                        {workspaceNameById.get(key.workspace_id) ?? '—'}
                      </TD>
                      <TD className="text-secondary">{userEmailById.get(key.user_id) ?? '—'}</TD>
                      <TD className="text-secondary">
                        {key.last_used_at ? new Date(key.last_used_at).toLocaleString() : 'Never'}
                      </TD>
                      <TD>
                        <StatusPill tone={statusTone(status)}>{status}</StatusPill>
                      </TD>
                      <TD className="text-right">
                        {status === 'revoked' ? (
                          <span className="text-[11px] text-muted">—</span>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Revoke ${key.name}`}
                            disabled={revokeKey.isPending}
                            onClick={() => onRevoke(key)}
                          >
                            Revoke
                          </Button>
                        )}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          ) : null}
        </div>
      </div>

      <Dialog open={open} onOpenChange={close}>
        <DialogContent
          title="Generate API key"
          description="Grants external /external/v1/chat access scoped to the chosen user's tenant context and the chosen workspace."
        >
          {createKey.data ? (
            <div className="space-y-3">
              <p className="text-[13px] text-ink">
                <strong>Copy it now — you won't be able to see it again.</strong>
              </p>
              <div>
                <Label htmlFor="generated-api-key">API key</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="generated-api-key"
                    readOnly
                    value={createKey.data.api_key}
                    onFocus={(e) => e.target.select()}
                    className="font-mono"
                  />
                  <Button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(createKey.data!.api_key);
                      toast('API key copied');
                    }}
                  >
                    Copy
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button variant="primary" onClick={() => close(false)}>
                  Done
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="space-y-3">
              <div>
                <Label htmlFor="key-name">Name</Label>
                <Input
                  id="key-name"
                  required
                  placeholder="e.g. CI integration"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="key-user">User</Label>
                <NativeSelect
                  id="key-user"
                  required
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                >
                  <option value="" disabled>
                    Select a user…
                  </option>
                  {(users.data ?? []).map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.email}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div>
                <Label htmlFor="key-workspace">Workspace</Label>
                <NativeSelect
                  id="key-workspace"
                  required
                  value={workspaceId}
                  onChange={(e) => setWorkspaceId(e.target.value)}
                >
                  <option value="" disabled>
                    Select a workspace…
                  </option>
                  {(workspaces.data ?? []).map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div>
                <Label htmlFor="key-expires">Expires (optional)</Label>
                <Input
                  id="key-expires"
                  type="date"
                  value={expiresAt}
                  onChange={(e) => setExpiresAt(e.target.value)}
                />
              </div>
              {createKey.isError ? (
                <p role="alert" className="text-[12px] text-danger">
                  {createKey.error.message}
                </p>
              ) : null}
              <DialogFooter>
                <Button onClick={() => close(false)}>Cancel</Button>
                <Button type="submit" variant="primary" disabled={createKey.isPending}>
                  {createKey.isPending ? 'Generating…' : 'Generate'}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
