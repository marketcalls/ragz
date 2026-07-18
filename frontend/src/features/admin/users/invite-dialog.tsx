import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useInvite } from './queries';

export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const invite = useInvite();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'admin' | 'user'>('user');

  const close = (next: boolean): void => {
    if (!next) {
      invite.reset();
      setEmail('');
      setRole('user');
    }
    onOpenChange(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    invite.mutate({ email, role });
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
