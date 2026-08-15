import { Pencil, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { useCreateOrganization, useOrganizations, useRenameOrganization, type Organization } from './queries';

function OrgFormDialog({
  open,
  onOpenChange,
  org = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present -> rename an existing org. Absent -> create a new one. */
  org?: Organization | null;
}) {
  const isEdit = org != null;
  const create = useCreateOrganization();
  const rename = useRenameOrganization();

  const [name, setName] = useState(org?.name ?? '');

  const pending = isEdit ? rename.isPending : create.isPending;
  const activeError = isEdit ? rename.error : create.error;

  const close = (next: boolean): void => {
    if (!next) {
      setName(org?.name ?? '');
      create.reset();
      rename.reset();
    }
    onOpenChange(next);
  };

  const handleSettled = {
    onSuccess: () => {
      toast(isEdit ? 'Organization renamed' : 'Organization created');
      close(false);
    },
    onError: (err: Error) => toast.error(err.message),
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    if (isEdit && org) {
      rename.mutate({ orgId: org.id, name }, handleSettled);
      return;
    }
    create.mutate(name, handleSettled);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={isEdit ? 'Rename organization' : 'New organization'}
        description={
          isEdit ? undefined : 'Organizations group workspaces, users, models, and quotas.'
        }
      >
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <Label htmlFor="org-name">Name</Label>
            <Input
              id="org-name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {activeError ? (
            <p role="alert" className="text-[12px] text-danger">
              {activeError.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {isEdit ? 'Save changes' : 'Create'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function OrganizationsPage() {
  const orgs = useOrganizations();
  // 'create' | a specific org being renamed | null (closed). `formKey` forces
  // a fresh mount on every open, mirroring RolesPage's formTarget pattern.
  const [formTarget, setFormTarget] = useState<'create' | Organization | null>(null);
  const [formKey, setFormKey] = useState(0);

  const openCreate = (): void => {
    setFormTarget('create');
    setFormKey((k) => k + 1);
  };
  const openEdit = (org: Organization): void => {
    setFormTarget(org);
    setFormKey((k) => k + 1);
  };

  return (
    <>
      <TopBar
        title="Organizations"
        actions={
          <Button variant="primary" size="sm" onClick={openCreate}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> New organization
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          {orgs.isPending ? <Spinner label="Loading organizations…" /> : null}
          {orgs.isError ? (
            <QueryError error={orgs.error} onRetry={() => orgs.refetch()} />
          ) : null}
          {orgs.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>SSO domains</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {orgs.data.map((org) => (
                  <TR key={org.id}>
                    <TD className="font-medium">{org.name}</TD>
                    <TD>
                      <div className="flex flex-wrap gap-1">
                        {org.sso_domains && org.sso_domains.length > 0 ? (
                          org.sso_domains.map((domain) => (
                            <StatusPill key={domain} tone="accent">
                              {domain}
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
                        aria-label={`Rename ${org.name}`}
                        onClick={() => openEdit(org)}
                      >
                        <Pencil className="h-4 w-4" aria-hidden />
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {orgs.data?.length === 0 ? (
            <p className="text-[13px] text-secondary">No organizations yet.</p>
          ) : null}
        </div>
      </div>
      <OrgFormDialog
        key={formKey}
        open={formTarget !== null}
        onOpenChange={(o) => !o && setFormTarget(null)}
        org={formTarget === 'create' ? null : formTarget}
      />
    </>
  );
}
