import { Plus } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';

import type { BotIntegrationOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';
import { useUsers } from '@/features/admin/users/queries';
import { useWorkspaces } from '@/features/workspaces/queries';

import { useBots, useCreateBot, useDeleteBot, useSetBotEnabled } from './queries';

type Platform = 'telegram' | 'discord' | 'slack';

const PLATFORMS: Platform[] = ['telegram', 'discord', 'slack'];

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function BotsPage() {
  const bots = useBots();
  const users = useUsers();
  const workspaces = useWorkspaces();
  const createBot = useCreateBot();
  const setEnabled = useSetBotEnabled();
  const deleteBot = useDeleteBot();

  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState<Platform>('telegram');
  const [name, setName] = useState('');
  const [userId, setUserId] = useState('');
  const [workspaceId, setWorkspaceId] = useState('');
  const [token, setToken] = useState('');
  const [signingSecret, setSigningSecret] = useState('');
  // Revocation is instant (the bot's webhook 404s the next call) and isn't
  // cheaply undoable (a replacement means re-registering a new webhook with
  // the platform) -- confirm before firing the DELETE, mirroring
  // api-keys-page.tsx's revoke-confirm / models-page.tsx's remove-confirm.
  const [removing, setRemoving] = useState<BotIntegrationOut | null>(null);

  const workspaceNameById = useMemo(
    () => new Map((workspaces.data ?? []).map((w): [string, string] => [w.id, w.name])),
    [workspaces.data],
  );
  const userEmailById = useMemo(
    () => new Map((users.data ?? []).map((u): [string, string] => [u.id, u.email])),
    [users.data],
  );

  const close = (next: boolean): void => {
    if (!next) {
      setPlatform('telegram');
      setName('');
      setUserId('');
      setWorkspaceId('');
      setToken('');
      setSigningSecret('');
      createBot.reset();
    }
    setOpen(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    createBot.mutate({
      platform,
      name,
      user_id: userId,
      workspace_id: workspaceId,
      token,
      signing_secret: signingSecret,
    });
  };

  const copyWebhookUrl = (url: string): void => {
    void navigator.clipboard.writeText(url);
    toast('Webhook URL copied');
  };

  return (
    <>
      <TopBar
        title="Bots"
        actions={
          <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add bot
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          {bots.isPending ? <Spinner label="Loading bots…" /> : null}
          {bots.isError ? <QueryError error={bots.error} onRetry={() => bots.refetch()} /> : null}
          {bots.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Platform</TH>
                  <TH>Workspace</TH>
                  <TH>User</TH>
                  <TH>Webhook URL</TH>
                  <TH>Enabled</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {bots.data.map((bot) => (
                  <TR key={bot.id}>
                    <TD className="font-medium">{bot.name}</TD>
                    <TD className="capitalize text-secondary">{bot.platform}</TD>
                    <TD className="text-secondary">
                      {workspaceNameById.get(bot.workspace_id) ?? '—'}
                    </TD>
                    <TD className="text-secondary">{userEmailById.get(bot.user_id) ?? '—'}</TD>
                    <TD>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Copy webhook URL for ${bot.name}`}
                        onClick={() => copyWebhookUrl(bot.webhook_url)}
                      >
                        Copy webhook URL
                      </Button>
                    </TD>
                    <TD>
                      <input
                        type="checkbox"
                        role="checkbox"
                        aria-label={bot.name}
                        checked={bot.enabled}
                        disabled={setEnabled.isPending}
                        onChange={(e) =>
                          setEnabled.mutate(
                            { id: bot.id, enabled: e.target.checked },
                            { onError: (err) => toast.error(err.message) },
                          )
                        }
                        className="h-4 w-4 accent-[var(--accent)]"
                      />
                    </TD>
                    <TD className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove ${bot.name}`}
                        disabled={deleteBot.isPending}
                        onClick={() => setRemoving(bot)}
                      >
                        Remove
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
        </div>
      </div>

      <Dialog open={open} onOpenChange={close}>
        <DialogContent
          title="Add bot"
          description="Connects a Telegram, Discord, or Slack bot to a workspace. Paste the bot token and signing secret from the platform's own developer dashboard -- Ragz never shows them back."
        >
          {createBot.data ? (
            <div className="space-y-3">
              <p className="text-[13px] text-ink">
                Paste this webhook URL into {createBot.data.platform}'s bot/webhook settings.
              </p>
              <div>
                <Label htmlFor="created-webhook-url">Webhook URL</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="created-webhook-url"
                    readOnly
                    value={createBot.data.webhook_url}
                    onFocus={(e) => e.target.select()}
                    className="font-mono"
                  />
                  <Button
                    type="button"
                    onClick={() => copyWebhookUrl(createBot.data!.webhook_url)}
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
                <Label htmlFor="bot-platform">Platform</Label>
                <NativeSelect
                  id="bot-platform"
                  required
                  value={platform}
                  onChange={(e) => setPlatform(e.target.value as Platform)}
                >
                  {PLATFORMS.map((p) => (
                    <option key={p} value={p}>
                      {capitalize(p)}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div>
                <Label htmlFor="bot-name">Name</Label>
                <Input
                  id="bot-name"
                  required
                  placeholder="e.g. Support bot"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="bot-user">User</Label>
                <NativeSelect
                  id="bot-user"
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
                <Label htmlFor="bot-workspace">Workspace</Label>
                <NativeSelect
                  id="bot-workspace"
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
                <Label htmlFor="bot-token">Bot token</Label>
                <Input
                  id="bot-token"
                  type="password"
                  required
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="bot-signing-secret">Signing secret</Label>
                <Input
                  id="bot-signing-secret"
                  type="password"
                  required
                  autoComplete="off"
                  value={signingSecret}
                  onChange={(e) => setSigningSecret(e.target.value)}
                />
              </div>
              {createBot.isError ? (
                <p role="alert" className="text-[12px] text-danger">
                  {createBot.error.message}
                </p>
              ) : null}
              <DialogFooter>
                <Button onClick={() => close(false)}>Cancel</Button>
                <Button type="submit" variant="primary" disabled={createBot.isPending}>
                  {createBot.isPending ? 'Adding…' : 'Add bot'}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent
          title="Remove bot"
          description={`"${removing?.name ?? ''}" will stop working immediately -- the platform's webhook call will start getting 404s. This can't be undone; a replacement bot integration must be created and its webhook URL re-registered with the platform.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteBot.isPending}
              onClick={() => {
                if (removing) {
                  deleteBot.mutate(removing.id, { onError: (err) => toast.error(err.message) });
                }
                setRemoving(null);
              }}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
