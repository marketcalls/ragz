import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { toast } from '@/components/ui/toaster';

import { useGroups, useSetGroupMembership } from '../groups/queries';

export function UserGroupsCell({ userId }: { userId: string }) {
  const groups = useGroups();
  const setMembership = useSetGroupMembership();
  const memberOf = (groups.data ?? []).filter((g) => g.member_ids.includes(userId));

  return (
    <Popover>
      <PopoverTrigger className="text-[12px] underline decoration-dotted">
        {memberOf.length > 0 ? memberOf.map((g) => g.name).join(', ') : 'no groups'}
      </PopoverTrigger>
      <PopoverContent className="w-56 p-2">
        {(groups.data ?? []).map((g) => (
          <label key={g.id} className="flex items-center gap-2 py-0.5 text-[13px]">
            <input
              type="checkbox"
              checked={g.member_ids.includes(userId)}
              disabled={setMembership.isPending}
              onChange={(e) =>
                setMembership.mutate(
                  { groupId: g.id, userId, member: e.target.checked },
                  { onError: (err) => toast.error(err.message) },
                )
              }
            />
            {g.name}
          </label>
        ))}
        {groups.data?.length === 0 ? (
          <p className="text-[12px] text-muted">Create groups from “Manage groups”.</p>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
