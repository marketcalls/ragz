import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';
import { useClaims } from '@/lib/use-claims';

import { useOrganizations } from '../organizations/queries';

import { useInvite } from './queries';

export function InviteDialog({
  open,
  onOpenChange,
  defaultOrgId,
  defaultRole,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-seeds the org selector (superadmin only) — e.g. the Organizations
   * page's "Invite admin" shortcut opening this dialog for a specific org. */
  defaultOrgId?: string;
  /** Pre-seeds the role field. */
  defaultRole?: 'admin' | 'user';
}) {
  const invite = useInvite();
  const claims = useClaims();
  const isSuperadmin = claims?.role === 'superadmin';
  // Non-superadmins always invite into their own org — skip the (superadmin-only)
  // orgs list request for them entirely.
  const orgs = useOrganizations({ enabled: isSuperadmin });

  const initialOrgId = (): string => defaultOrgId ?? claims?.org ?? '';

  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'admin' | 'user'>(defaultRole ?? 'user');
  const [orgId, setOrgId] = useState(initialOrgId);

  const close = (next: boolean): void => {
    if (!next) {
      invite.reset();
      setEmail('');
      setRole(defaultRole ?? 'user');
      setOrgId(initialOrgId());
    }
    onOpenChange(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    invite.mutate(isSuperadmin ? { email, role, org_id: orgId } : { email, role });
  };

  const inviteLink = invite.data
    ? `${window.location.origin}/invite?token=${invite.data.invite_token}`
    : null;

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Invite a user"
        description="Phase 1 has no mailer — copy the link and send it yourself. It is shown once."
      >
        {inviteLink ? (
          <div className="space-y-3">
            <p className="break-all rounded-md border border-line bg-subtle p-2 font-mono text-[12px] text-ink">
              {inviteLink}
            </p>
            <DialogFooter>
              <Button
                variant="primary"
                onClick={() => {
                  void navigator.clipboard.writeText(inviteLink);
                  toast('Invite link copied');
                }}
              >
                Copy link
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-3">
            <div>
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              <NativeSelect
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as 'admin' | 'user')}
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </NativeSelect>
            </div>
            {isSuperadmin ? (
              <div>
                <Label htmlFor="invite-org">Organization</Label>
                <NativeSelect
                  id="invite-org"
                  value={orgId}
                  onChange={(e) => setOrgId(e.target.value)}
                >
                  {(orgs.data ?? []).map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            ) : null}
            {invite.isError ? (
              <p role="alert" className="text-[12px] text-danger">
                {invite.error.message}
              </p>
            ) : null}
            <DialogFooter>
              <Button onClick={() => close(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={invite.isPending}>
                Send invite
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
