import { Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useCreateGroup, useDeleteGroup, useGroups } from './queries';

export function GroupsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const groups = useGroups();
  const createGroup = useCreateGroup();
  const deleteGroup = useDeleteGroup();
  const [name, setName] = useState('');

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    createGroup.mutate(name.trim(), {
      onSuccess: () => setName(''),
      onError: (err) => toast.error(err.message),
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Groups"
        description="Groups gate document access. Deleting a group keeps its documents restricted until an admin re-edits their access."
      >
        <form onSubmit={onSubmit} className="flex gap-2">
          <Input
            aria-label="New group name"
            placeholder="e.g. finance"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" variant="primary" disabled={createGroup.isPending}>
            Create
          </Button>
        </form>
        {groups.isPending ? <Spinner label="Loading groups…" /> : null}
        <ul className="mt-3 space-y-1">
          {groups.data?.map((g) => (
            <li key={g.id} className="flex items-center justify-between text-[13px]">
              <span>
                {g.name}{' '}
                <span className="text-muted">
                  ({g.member_ids.length} member{g.member_ids.length === 1 ? '' : 's'})
                </span>
              </span>
              <Button
                size="sm"
                aria-label={`Delete group ${g.name}`}
                onClick={() =>
                  deleteGroup.mutate(g.id, { onError: (err) => toast.error(err.message) })
                }
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </li>
          ))}
          {groups.data?.length === 0 ? (
            <li className="text-[13px] text-muted">No groups yet.</li>
          ) : null}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
