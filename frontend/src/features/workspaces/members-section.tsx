import { useMemo, useState } from 'react';

import type { MemberOut, UserOut, WorkspaceRole } from '@/api/types';
import { useUsers } from '@/features/admin/users/queries';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useAddMember, useRemoveMember, useUpdateMemberRole, useWorkspaceMembers } from './queries';

// "owner"/"manager" are department admins per the governance design
// (docs/superpowers/specs) -- labeled explicitly here so admins mapping
// users onto a workspace understand the consequence of each role.
const ROLE_OPTIONS: { value: WorkspaceRole; label: string }[] = [
  { value: 'owner', label: 'Owner — dept admin' },
  { value: 'manager', label: 'Manager — dept admin' },
  { value: 'contributor', label: 'Contributor' },
  { value: 'viewer', label: 'Viewer' },
];

function emailFor(member: MemberOut, usersById: Map<string, UserOut>): string {
  return usersById.get(member.user_id)?.email ?? member.user_id;
}

// H-C? mount point: RBAC-08's member list/change-role/remove backend
// endpoints existed with nothing in the app calling them -- this is the
// admin surface for mapping org users onto a workspace/department and
// designating department admins (owner/manager).
export function MembersSection({ workspaceId }: { workspaceId: string }) {
  const members = useWorkspaceMembers(workspaceId);
  const users = useUsers();
  const addMember = useAddMember(workspaceId);
  const updateRole = useUpdateMemberRole(workspaceId);
  const removeMember = useRemoveMember(workspaceId);

  const [newUserId, setNewUserId] = useState('');
  const [newRole, setNewRole] = useState<WorkspaceRole>('contributor');

  const usersById = useMemo(() => {
    const map = new Map<string, UserOut>();
    for (const u of users.data ?? []) map.set(u.id, u);
    return map;
  }, [users.data]);

  const memberIds = useMemo(
    () => new Set((members.data ?? []).map((m) => m.user_id)),
    [members.data],
  );
  const availableUsers = (users.data ?? []).filter((u) => !memberIds.has(u.id));

  if (members.isPending || users.isPending) {
    return <Spinner label="Loading members…" />;
  }
  if (members.isError) {
    return (
      <p role="alert" className="text-[13px] text-danger">
        Failed to load members.
      </p>
    );
  }

  const onAdd = (): void => {
    if (!newUserId) return;
    addMember.mutate(
      { user_id: newUserId, role: newRole },
      {
        onSuccess: () => {
          setNewUserId('');
          setNewRole('contributor');
        },
        onError: (err: Error) => toast.error(err.message),
      },
    );
  };

  return (
    <div className="space-y-3">
      <h3 className="text-[13px] font-semibold text-ink">Members</h3>
      <p className="text-[12px] text-secondary">
        Owners and managers are department admins — they can manage this department&apos;s
        members and see its usage reports.
      </p>
      <ul className="space-y-2">
        {(members.data ?? []).map((m) => {
          const email = emailFor(m, usersById);
          return (
            <li key={m.user_id} className="flex items-center justify-between gap-2 text-[13px]">
              <span className="truncate">{email}</span>
              <div className="flex shrink-0 items-center gap-2">
                <NativeSelect
                  aria-label={`Role for ${email}`}
                  value={m.role}
                  onChange={(e) =>
                    updateRole.mutate(
                      { userId: m.user_id, role: e.target.value as WorkspaceRole },
                      { onError: (err: Error) => toast.error(err.message) },
                    )
                  }
                  className="w-44"
                >
                  {ROLE_OPTIONS.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </NativeSelect>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={removeMember.isPending}
                  onClick={() =>
                    removeMember.mutate(m.user_id, {
                      onError: (err: Error) => toast.error(err.message),
                    })
                  }
                >
                  Remove
                </Button>
              </div>
            </li>
          );
        })}
        {members.data?.length === 0 ? (
          <li className="text-[13px] text-muted">No members yet.</li>
        ) : null}
      </ul>
      <div className="flex items-end gap-2 border-t border-line pt-3">
        <div className="flex-1 space-y-1">
          <Label htmlFor="member-add-user">Add member</Label>
          <NativeSelect
            id="member-add-user"
            value={newUserId}
            onChange={(e) => setNewUserId(e.target.value)}
          >
            <option value="">Select a user…</option>
            {availableUsers.map((u) => (
              <option key={u.id} value={u.id}>
                {u.email}
              </option>
            ))}
          </NativeSelect>
        </div>
        <NativeSelect
          aria-label="Role for new member"
          value={newRole}
          onChange={(e) => setNewRole(e.target.value as WorkspaceRole)}
          className="w-44"
        >
          {ROLE_OPTIONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </NativeSelect>
        <Button onClick={onAdd} disabled={!newUserId || addMember.isPending}>
          Add
        </Button>
      </div>
    </div>
  );
}
