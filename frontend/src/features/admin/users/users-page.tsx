import { UserPlus, Users as UsersIcon } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import type { UserOut, UserQuotaOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { GroupsDialog } from '../groups/groups-dialog';
import { useAssignCustomRole, useRoles } from '../roles/queries';

import { InviteDialog } from './invite-dialog';
import { usePatchUser, useSetUserQuota, useUserQuota, useUsers } from './queries';
import { UserGroupsCell } from './user-groups-cell';

/** Pre-filled directly from the settled query result (same synchronous-prop
 * seeding trick as OrgQuotaDialog/ModelFormDialog) — rendered only once
 * useUserQuota has data, so there is no async-refill-after-mount race. */
function UserQuotaFields({
  userId,
  quota,
  onDone,
}: {
  userId: string;
  quota: UserQuotaOut;
  onDone: () => void;
}) {
  const setUserQuota = useSetUserQuota();
  const [override, setOverride] = useState(
    quota.monthly_tokens != null ? String(quota.monthly_tokens) : '',
  );

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    setUserQuota.mutate(
      { userId, monthlyTokens: override === '' ? null : Number(override) },
      {
        onSuccess: () => {
          toast('User quota saved');
          onDone();
        },
        onError: (err: Error) => toast.error(err.message),
      },
    );
  };

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <p className="text-[12px] text-secondary">
        Used {quota.used_tokens.toLocaleString()} /{' '}
        {quota.allocated_tokens != null ? quota.allocated_tokens.toLocaleString() : '—'} tokens
        this period · resets {new Date(quota.resets_at).toLocaleDateString()}
      </p>
      <div>
        <Label htmlFor="user-quota-override">Monthly token override</Label>
        <Input
          id="user-quota-override"
          type="number"
          min={0}
          placeholder="Use org default"
          value={override}
          onChange={(e) => setOverride(e.target.value)}
        />
      </div>
      {setUserQuota.isError ? (
        <p role="alert" className="text-[12px] text-danger">
          {setUserQuota.error.message}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={setUserQuota.isPending}>
          Save
        </Button>
      </DialogFooter>
    </form>
  );
}

function UserQuotaDialog({
  user,
  onOpenChange,
}: {
  user: UserOut | null;
  onOpenChange: (open: boolean) => void;
}) {
  const quota = useUserQuota(user?.id ?? '', user !== null);

  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent
        title={user ? `Quota — ${user.email}` : 'Quota'}
        description="Override this user's monthly token allocation, or leave blank to use the org default."
      >
        {quota.isPending ? <Spinner label="Loading quota…" /> : null}
        {quota.isError ? (
          <p role="alert" className="text-[12px] text-danger">
            Failed to load user quota.
          </p>
        ) : null}
        {user && quota.data ? (
          <UserQuotaFields
            userId={user.id}
            quota={quota.data}
            onDone={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export function UsersPage() {
  const users = useUsers();
  const roles = useRoles();
  const patchUser = usePatchUser();
  const assignCustomRole = useAssignCustomRole();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [groupsOpen, setGroupsOpen] = useState(false);
  const [confirmUser, setConfirmUser] = useState<UserOut | null>(null);
  const [quotaUser, setQuotaUser] = useState<UserOut | null>(null);

  return (
    <>
      <TopBar
        title="Users"
        actions={
          <>
            <Button size="sm" onClick={() => setGroupsOpen(true)}>
              <UsersIcon className="h-3.5 w-3.5" aria-hidden /> Manage groups
            </Button>
            <Button variant="primary" size="sm" onClick={() => setInviteOpen(true)}>
              <UserPlus className="h-3.5 w-3.5" aria-hidden /> Invite
            </Button>
          </>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-3xl">
          {users.isPending ? <Spinner label="Loading users…" /> : null}
          {users.isError ? (
            <QueryError error={users.error} onRetry={() => users.refetch()} />
          ) : null}
          {users.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Email</TH>
                  <TH>Role</TH>
                  <TH>Custom role</TH>
                  <TH>Status</TH>
                  <TH>Groups</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {users.data.map((user) => (
                  <TR key={user.id}>
                    <TD className="font-medium">{user.email}</TD>
                    <TD>
                      {user.role === 'superadmin' ? (
                        <span className="text-secondary">superadmin</span>
                      ) : (
                        <NativeSelect
                          aria-label={`Role for ${user.email}`}
                          className="w-28"
                          value={user.role}
                          disabled={patchUser.isPending}
                          onChange={(e) =>
                            patchUser.mutate(
                              {
                                userId: user.id,
                                body: { role: e.target.value as 'admin' | 'user' },
                              },
                              { onError: (err) => toast.error(err.message) },
                            )
                          }
                        >
                          <option value="user">User</option>
                          <option value="admin">Admin</option>
                        </NativeSelect>
                      )}
                    </TD>
                    <TD>
                      {user.role === 'user' ? (
                        <NativeSelect
                          aria-label={`Custom role for ${user.email}`}
                          className="w-32"
                          value={user.custom_role_id ?? ''}
                          disabled={assignCustomRole.isPending}
                          onChange={(e) =>
                            assignCustomRole.mutate(
                              { userId: user.id, roleTemplateId: e.target.value || null },
                              { onError: (err) => toast.error(err.message) },
                            )
                          }
                        >
                          <option value="">Default</option>
                          {(roles.data ?? []).map((role) => (
                            <option key={role.id} value={role.id}>
                              {role.name}
                            </option>
                          ))}
                        </NativeSelect>
                      ) : (
                        <span className="text-secondary">—</span>
                      )}
                    </TD>
                    <TD>
                      <StatusPill tone={user.active ? 'success' : 'danger'}>
                        {user.active ? 'Active' : 'Deactivated'}
                      </StatusPill>
                    </TD>
                    <TD>
                      {user.role !== 'superadmin' ? <UserGroupsCell userId={user.id} /> : null}
                    </TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button size="sm" onClick={() => setQuotaUser(user)}>
                          Quota
                        </Button>
                        {user.role !== 'superadmin' ? (
                          <Button size="sm" onClick={() => setConfirmUser(user)}>
                            {user.active ? 'Deactivate' : 'Reactivate'}
                          </Button>
                        ) : null}
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
        </div>
      </div>
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
      <GroupsDialog open={groupsOpen} onOpenChange={setGroupsOpen} />
      <UserQuotaDialog user={quotaUser} onOpenChange={(o) => !o && setQuotaUser(null)} />
      <Dialog open={confirmUser !== null} onOpenChange={(o) => !o && setConfirmUser(null)}>
        <DialogContent
          title={confirmUser?.active ? 'Deactivate user' : 'Reactivate user'}
          description={
            confirmUser?.active
              ? `${confirmUser.email} will immediately lose access.`
              : `${confirmUser?.email ?? ''} will regain access.`
          }
        >
          <DialogFooter>
            <Button onClick={() => setConfirmUser(null)}>Cancel</Button>
            <Button
              variant={confirmUser?.active ? 'danger' : 'primary'}
              onClick={() => {
                if (confirmUser) {
                  patchUser.mutate(
                    { userId: confirmUser.id, body: { active: !confirmUser.active } },
                    { onError: (err) => toast.error(err.message) },
                  );
                }
                setConfirmUser(null);
              }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
