import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { RoleTemplateOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { PERMISSION_LABELS, RoleFormDialog } from './role-form-dialog';
import { useDeleteRole, useRoles } from './queries';

export function RolesPage() {
  const roles = useRoles();
  const deleteRole = useDeleteRole();
  // 'create' | a specific role being edited | null (closed). `formKey` forces
  // a fresh mount on every open, mirroring ModelsPage's formTarget pattern.
  const [formTarget, setFormTarget] = useState<'create' | RoleTemplateOut | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [removing, setRemoving] = useState<RoleTemplateOut | null>(null);

  const openCreate = (): void => {
    setFormTarget('create');
    setFormKey((k) => k + 1);
  };
  const openEdit = (role: RoleTemplateOut): void => {
    setFormTarget(role);
    setFormKey((k) => k + 1);
  };

  return (
    <>
      <TopBar
        title="Roles"
        actions={
          <Button variant="primary" size="sm" onClick={openCreate}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add role
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          {roles.isPending ? <Spinner label="Loading roles…" /> : null}
          {roles.isError ? (
            <QueryError error={roles.error} onRetry={() => roles.refetch()} />
          ) : null}
          {roles.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Description</TH>
                  <TH>Permissions</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {roles.data.map((role) => (
                  <TR key={role.id}>
                    <TD className="font-medium">{role.name}</TD>
                    <TD className="text-secondary">{role.description || '—'}</TD>
                    <TD>
                      <div className="flex flex-wrap gap-1">
                        {role.permissions.length > 0 ? (
                          role.permissions.map((p) => (
                            <StatusPill key={p} tone="accent">
                              {PERMISSION_LABELS[p] ?? p}
                            </StatusPill>
                          ))
                        ) : (
                          <span className="text-secondary">none</span>
                        )}
                      </div>
                    </TD>
                    <TD className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Edit ${role.name}`}
                        onClick={() => openEdit(role)}
                      >
                        <Pencil className="h-4 w-4" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${role.name}`}
                        onClick={() => setRemoving(role)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {roles.data?.length === 0 ? (
            <p className="text-[13px] text-secondary">No custom roles yet.</p>
          ) : null}
        </div>
      </div>
      <RoleFormDialog
        key={formKey}
        open={formTarget !== null}
        onOpenChange={(o) => !o && setFormTarget(null)}
        role={formTarget === 'create' ? null : formTarget}
      />
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent
          title="Delete role"
          description={`"${removing?.name ?? ''}" will be removed. This fails if any user is still assigned it.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteRole.isPending}
              onClick={() => {
                if (removing) {
                  deleteRole.mutate(removing.id, { onError: (err) => toast.error(err.message) });
                }
                setRemoving(null);
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
