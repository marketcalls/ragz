import { useState, type FormEvent } from 'react';

import type { OrgQuotaIn, OrgQuotaOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useOrgQuota, usePutOrgQuota } from './queries';

/** Pre-filled directly from the settled query result (mirrors
 * ModelFormDialog/RoleFormDialog's pattern of seeding useState from a
 * synchronously-known value) — rendered only once useOrgQuota has settled,
 * so there is no async-refill-after-mount race. */
function OrgQuotaForm({
  orgId,
  quota,
  usageTokens,
  onDone,
}: {
  orgId: string;
  quota: OrgQuotaOut | null;
  usageTokens: number;
  onDone: () => void;
}) {
  const putQuota = usePutOrgQuota(orgId);
  const [monthlyTokens, setMonthlyTokens] = useState(
    quota ? String(quota.monthly_tokens) : '',
  );
  const [defaultUserTokens, setDefaultUserTokens] = useState(
    quota?.default_user_monthly_tokens != null ? String(quota.default_user_monthly_tokens) : '',
  );
  const [resetDay, setResetDay] = useState(String(quota?.reset_day ?? 1));

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    const body: OrgQuotaIn = {
      monthly_tokens: Number(monthlyTokens),
      default_user_monthly_tokens: defaultUserTokens === '' ? null : Number(defaultUserTokens),
      reset_day: Number(resetDay),
    };
    putQuota.mutate(body, {
      onSuccess: () => {
        toast('Org quota saved');
        onDone();
      },
      onError: (err: Error) => toast.error(err.message),
    });
  };

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <p className="text-[12px] text-secondary">
        Current period usage (last 30 days): {usageTokens.toLocaleString()} tokens
      </p>
      <div>
        <Label htmlFor="org-quota-monthly">Monthly tokens</Label>
        <Input
          id="org-quota-monthly"
          type="number"
          min={0}
          required
          value={monthlyTokens}
          onChange={(e) => setMonthlyTokens(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="org-quota-default-user">Default user allocation</Label>
        <Input
          id="org-quota-default-user"
          type="number"
          min={0}
          placeholder="No per-user default"
          value={defaultUserTokens}
          onChange={(e) => setDefaultUserTokens(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="org-quota-reset-day">Reset day</Label>
        <Input
          id="org-quota-reset-day"
          type="number"
          min={1}
          max={31}
          required
          value={resetDay}
          onChange={(e) => setResetDay(e.target.value)}
        />
      </div>
      {putQuota.isError ? (
        <p role="alert" className="text-[12px] text-danger">
          {putQuota.error.message}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={putQuota.isPending}>
          Save
        </Button>
      </DialogFooter>
    </form>
  );
}

export function OrgQuotaDialog({
  open,
  onOpenChange,
  orgId,
  orgName,
  usageTokens,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orgId: string;
  orgName: string;
  /** Current-period usage for this org, sourced from the caller's already-loaded
   * per-org usage row (e.g. the health page's org table) — not re-fetched here,
   * since useUsageSummary is scoped to the calling admin's own org and would be
   * wrong for an arbitrary target org. */
  usageTokens: number;
}) {
  const orgQuota = useOrgQuota(orgId, open);
  const close = (next: boolean): void => onOpenChange(next);
  const settled = !orgQuota.isPending && !orgQuota.isError;

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={`Quota — ${orgName}`}
        description="Monthly token allocation for this organization."
      >
        {orgQuota.isPending ? <Spinner label="Loading org quota…" /> : null}
        {orgQuota.isError ? (
          <p role="alert" className="text-[12px] text-danger">
            Failed to load org quota.
          </p>
        ) : null}
        {settled ? (
          <OrgQuotaForm
            orgId={orgId}
            quota={orgQuota.data ?? null}
            usageTokens={usageTokens}
            onDone={() => close(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
