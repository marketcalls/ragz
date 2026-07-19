import { useState, type FormEvent } from 'react';

import type { RoleTemplateOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/toaster';

import { useCreateRole, useUpdateRole, type RoleTemplateInput } from './queries';

export const PERMISSION_LABELS: Record<string, string> = {
  'documents.upload': 'Upload documents',
  'documents.delete': 'Delete documents',
  'workspace.configure': 'Configure workspace settings',
  'analytics.view': 'View usage analytics',
  'chat.use': 'Use chat',
};

const PERMISSION_KEYS = Object.keys(PERMISSION_LABELS);

export function RoleFormDialog({
  open,
  onOpenChange,
  role = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present -> edit an existing role template. Absent -> create a new one. */
  role?: RoleTemplateOut | null;
}) {
  const isEdit = role != null;
  const create = useCreateRole();
  const update = useUpdateRole();

  const [name, setName] = useState(role?.name ?? '');
  const [description, setDescription] = useState(role?.description ?? '');
  const [permissions, setPermissions] = useState<Set<string>>(
    new Set(role?.permissions ?? []),
  );

  const pending = isEdit ? update.isPending : create.isPending;
  const activeError = isEdit ? update.error : create.error;

  const togglePermission = (key: string, checked: boolean): void => {
    setPermissions((prev) => {
      const next = new Set(prev);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  const close = (next: boolean): void => {
    if (!next) {
      setName(role?.name ?? '');
      setDescription(role?.description ?? '');
      setPermissions(new Set(role?.permissions ?? []));
      create.reset();
      update.reset();
    }
    onOpenChange(next);
  };

  const handleSettled = {
    onSuccess: () => {
      toast(isEdit ? 'Role updated' : 'Role created');
      close(false);
    },
    onError: (err: Error) => toast.error(err.message),
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    const body: RoleTemplateInput = {
      name,
      description,
      permissions: PERMISSION_KEYS.filter((key) => permissions.has(key)),
    };
    if (isEdit && role) {
      update.mutate({ roleId: role.id, body }, handleSettled);
      return;
    }
    create.mutate(body, handleSettled);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={isEdit ? 'Edit role' : 'Add role'}
        description="Custom roles grant a fixed set of permissions to 'user'-tier accounts."
      >
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <Label htmlFor="role-name">Name</Label>
            <Input id="role-name" required value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="role-description">Description</Label>
            <Input
              id="role-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div>
            <Label>Permissions</Label>
            <div className="mt-1 space-y-1.5">
              {PERMISSION_KEYS.map((key) => (
                <label key={key} className="flex items-center gap-2 text-[13px] text-ink">
                  <input
                    type="checkbox"
                    aria-label={PERMISSION_LABELS[key]}
                    checked={permissions.has(key)}
                    onChange={(e) => togglePermission(key, e.target.checked)}
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                  {PERMISSION_LABELS[key]}
                </label>
              ))}
            </div>
          </div>
          {activeError ? (
            <p role="alert" className="text-[12px] text-danger">
              {activeError.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {isEdit ? 'Save changes' : 'Add role'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
