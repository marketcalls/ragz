import { Pencil, Plus, Trash2, UserPlus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

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

import { InviteDialog } from '../users/invite-dialog';

import {
  useCreateOrganization,
  useDeleteOrganization,
  useOrganizations,
  useUpdateOrganization,
  type Organization,
} from './queries';

const INDUSTRY_OPTIONS = [
  'Technology',
  'Financial Services',
  'Healthcare',
  'Education',
  'Manufacturing',
  'Retail & E-commerce',
  'Media',
  'Legal',
  'Government',
  'Nonprofit',
  'Other',
];

const COMPANY_SIZE_OPTIONS = ['1–10', '11–50', '51–200', '201–500', '501–1000', '1000+'];

function OrgFormDialog({
  open,
  onOpenChange,
  org = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present -> edit an existing org. Absent -> create a new one. */
  org?: Organization | null;
}) {
  const isEdit = org != null;
  const create = useCreateOrganization();
  const update = useUpdateOrganization();

  const [name, setName] = useState(org?.name ?? '');
  const [contactEmail, setContactEmail] = useState(org?.contact_email ?? '');
  const [industry, setIndustry] = useState(org?.industry ?? '');
  const [companySize, setCompanySize] = useState(org?.company_size ?? '');
  const [country, setCountry] = useState(org?.country ?? '');

  const pending = isEdit ? update.isPending : create.isPending;
  const activeError = isEdit ? update.error : create.error;

  const reset = (): void => {
    setName(org?.name ?? '');
    setContactEmail(org?.contact_email ?? '');
    setIndustry(org?.industry ?? '');
    setCompanySize(org?.company_size ?? '');
    setCountry(org?.country ?? '');
  };

  const close = (next: boolean): void => {
    if (!next) {
      reset();
      create.reset();
      update.reset();
    }
    onOpenChange(next);
  };

  const handleSettled = {
    onSuccess: () => {
      toast(isEdit ? 'Organization updated' : 'Organization created');
      close(false);
    },
    onError: (err: Error) => toast.error(err.message),
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    const profile = {
      contact_email: contactEmail || null,
      industry: industry || null,
      company_size: companySize || null,
      country: country || null,
    };
    if (isEdit && org) {
      update.mutate({ orgId: org.id, name, ...profile }, handleSettled);
      return;
    }
    create.mutate({ name, ...profile }, handleSettled);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={isEdit ? 'Edit organization' : 'New organization'}
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
          <div>
            <Label htmlFor="org-contact-email">Contact email</Label>
            <Input
              id="org-contact-email"
              type="email"
              value={contactEmail ?? ''}
              onChange={(e) => setContactEmail(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="org-industry">Industry</Label>
            <NativeSelect
              id="org-industry"
              value={industry ?? ''}
              onChange={(e) => setIndustry(e.target.value)}
            >
              <option value="">—</option>
              {INDUSTRY_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div>
            <Label htmlFor="org-company-size">Company size</Label>
            <NativeSelect
              id="org-company-size"
              value={companySize ?? ''}
              onChange={(e) => setCompanySize(e.target.value)}
            >
              <option value="">—</option>
              {COMPANY_SIZE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div>
            <Label htmlFor="org-country">Country</Label>
            <Input
              id="org-country"
              value={country ?? ''}
              onChange={(e) => setCountry(e.target.value)}
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
  const deleteOrg = useDeleteOrganization();
  // 'create' | a specific org being edited | null (closed). `formKey` forces
  // a fresh mount on every open, mirroring RolesPage's formTarget pattern.
  const [formTarget, setFormTarget] = useState<'create' | Organization | null>(null);
  const [formKey, setFormKey] = useState(0);
  // "Invite admin" per row (below): a fresh InviteDialog mount per open so
  // its defaultOrgId/defaultRole seed cleanly, mirroring formKey above.
  const [inviteTarget, setInviteTarget] = useState<Organization | null>(null);
  const [inviteKey, setInviteKey] = useState(0);
  const [removing, setRemoving] = useState<Organization | null>(null);

  const openCreate = (): void => {
    setFormTarget('create');
    setFormKey((k) => k + 1);
  };
  const openEdit = (org: Organization): void => {
    setFormTarget(org);
    setFormKey((k) => k + 1);
  };
  const openInviteAdmin = (org: Organization): void => {
    setInviteTarget(org);
    setInviteKey((k) => k + 1);
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
                        aria-label={`Invite admin to ${org.name}`}
                        onClick={() => openInviteAdmin(org)}
                      >
                        <UserPlus className="h-4 w-4" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Edit ${org.name}`}
                        onClick={() => openEdit(org)}
                      >
                        <Pencil className="h-4 w-4" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${org.name}`}
                        onClick={() => setRemoving(org)}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
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
        key={`form-${formKey}`}
        open={formTarget !== null}
        onOpenChange={(o) => !o && setFormTarget(null)}
        org={formTarget === 'create' ? null : formTarget}
      />
      <InviteDialog
        key={`invite-${inviteKey}`}
        open={inviteTarget !== null}
        onOpenChange={(o) => !o && setInviteTarget(null)}
        defaultOrgId={inviteTarget?.id}
        defaultRole="admin"
      />
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent
          title="Delete organization"
          description={`Delete organization "${removing?.name ?? ''}"? This can't be undone.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteOrg.isPending}
              onClick={() => {
                if (removing) {
                  deleteOrg.mutate(removing.id, { onError: (err) => toast.error(err.message) });
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
