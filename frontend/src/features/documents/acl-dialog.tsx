import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api } from '@/api/client';
import type { DocumentOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { toast } from '@/components/ui/toaster';

import { useGroups } from '../admin/groups/queries';

export function AclDialog({
  document,
  open,
  onOpenChange,
}: {
  document: DocumentOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const groups = useGroups();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>(document.acl_group_ids ?? []);
  const restricted = selected.length > 0;

  const save = useMutation({
    mutationFn: async (aclGroupIds: string[] | null) => {
      const { error } = await api.PUT('/api/v1/documents/{document_id}/acl', {
        params: { path: { document_id: document.id } },
        body: { acl_group_ids: aclGroupIds },
      });
      if (error) throw new Error('failed to update document access');
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents'] });
      onOpenChange(false);
    },
    onError: (err) => toast.error(err.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Access for ${document.filename}`}
        description="No groups selected = everyone in the workspace. Selecting groups restricts retrieval and citations to their members."
      >
        <div className="space-y-1">
          {(groups.data ?? []).map((g) => (
            <label key={g.id} className="flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={selected.includes(g.id)}
                onChange={(e) =>
                  setSelected((prev) =>
                    e.target.checked ? [...prev, g.id] : prev.filter((id) => id !== g.id),
                  )
                }
              />
              {g.name}
            </label>
          ))}
        </div>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={save.isPending}
            onClick={() => save.mutate(restricted ? selected : null)}
          >
            {restricted ? `Restrict to ${selected.length} group(s)` : 'Make unrestricted'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
