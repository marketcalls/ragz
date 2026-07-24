import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';

import type { CatalogEntryOut, ModelOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill, type StatusTone } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { ModelFormDialog } from './model-form-dialog';
import { useAdminModels, useCatalog, useDeleteModel, usePatchModel } from './queries';

function syncTone(status: ModelOut['sync_status']): StatusTone {
  if (status === 'synced') return 'success';
  if (status === 'error') return 'danger';
  return 'accent';
}

export function ModelsPage() {
  const models = useAdminModels();
  const catalog = useCatalog();
  const patchModel = usePatchModel();
  const deleteModel = useDeleteModel();
  // 'create' | a specific model being edited | null (closed). `formKey` forces
  // a fresh mount on every open so the dialog's fields always start from the
  // current target's data instead of stale state from a previous session.
  const [formTarget, setFormTarget] = useState<'create' | ModelOut | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [removing, setRemoving] = useState<ModelOut | null>(null);
  const [tab, setTab] = useState<'chat' | 'embedding'>('chat');

  const catalogByName = useMemo(
    () => new Map((catalog.data?.entries ?? []).map((e): [string, CatalogEntryOut] => [e.name, e])),
    [catalog.data],
  );

  const openCreate = (): void => {
    setFormTarget('create');
    setFormKey((k) => k + 1);
  };
  const openEdit = (model: ModelOut): void => {
    setFormTarget(model);
    setFormKey((k) => k + 1);
  };

  return (
    <>
      <TopBar
        title="Models"
        actions={
          <Button variant="primary" size="sm" onClick={openCreate}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add model
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          <div className="mb-3 flex gap-1">
            {(['chat', 'embedding'] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`rounded-md px-2 py-1 text-xs capitalize ${
                  tab === t ? 'bg-subtle text-ink' : 'text-secondary'
                }`}
              >
                {t} models
              </button>
            ))}
          </div>
          {models.isPending ? <Spinner label="Loading models…" /> : null}
          {models.isError ? (
            <QueryError error={models.error} onRetry={() => models.refetch()} />
          ) : null}
          {models.data ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Provider</TH>
                  <TH>Type</TH>
                  <TH>Model id</TH>
                  <TH>Key</TH>
                  <TH>Gateway</TH>
                  <TH>Enabled</TH>
                  <TH>Utility</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {models.data
                  .filter((model) => model.modality === tab)
                  .map((model) => {
                  const catalogEntry = catalogByName.get(model.litellm_model_name);
                  return (
                    <TR key={model.id}>
                      <TD className="font-medium">{model.display_name}</TD>
                      <TD className="text-secondary">{model.provider_kind}</TD>
                      <TD className="capitalize text-secondary">{model.modality}</TD>
                      <TD className="font-mono text-[12px] text-secondary">
                        <div>{model.litellm_model_name}</div>
                        {catalogEntry ? (
                          <span className="text-xs text-muted tabular-nums">
                            {catalogEntry.max_input_tokens
                              ? `${Math.round(catalogEntry.max_input_tokens / 1000)}k ctx`
                              : null}
                            {catalogEntry.input_cost_per_1m != null
                              ? ` · $${catalogEntry.input_cost_per_1m.toFixed(2)}/$${(
                                  catalogEntry.output_cost_per_1m ?? 0
                                ).toFixed(2)} per 1M`
                              : null}
                          </span>
                        ) : null}
                      </TD>
                      <TD className="font-mono text-[12px] text-muted">
                        {model.key_fingerprint ?? '—'}
                      </TD>
                      <TD>
                        <StatusPill tone={syncTone(model.sync_status)}>
                          {model.sync_status}
                        </StatusPill>
                      </TD>
                      <TD>
                        <input
                          type="checkbox"
                          aria-label={`Enable ${model.display_name}`}
                          checked={model.enabled}
                          disabled={patchModel.isPending}
                          onChange={(e) =>
                            patchModel.mutate(
                              { modelId: model.id, body: { enabled: e.target.checked } },
                              { onError: (err) => toast.error(err.message) },
                            )
                          }
                          className="h-4 w-4 accent-[var(--accent)]"
                        />
                      </TD>
                      <TD>
                        <input
                          type="radio"
                          name="utility-model"
                          aria-label={`Use ${model.display_name} as the utility model`}
                          checked={model.is_utility}
                          disabled={patchModel.isPending}
                          onChange={() =>
                            patchModel.mutate(
                              { modelId: model.id, body: { is_utility: true } },
                              { onError: (err) => toast.error(err.message) },
                            )
                          }
                          className="h-4 w-4 accent-[var(--accent)]"
                        />
                      </TD>
                      <TD className="text-right">
                        {model.provider_kind === 'tei' ? (
                          <span className="text-[11px] text-muted">Built-in</span>
                        ) : (
                          <>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Edit ${model.display_name}`}
                              onClick={() => openEdit(model)}
                            >
                              <Pencil className="h-4 w-4" aria-hidden />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Remove ${model.display_name}`}
                              onClick={() => setRemoving(model)}
                            >
                              <Trash2 className="h-4 w-4" aria-hidden />
                            </Button>
                          </>
                        )}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          ) : null}
          <p className="mt-2 text-[12px] text-muted">
            The utility model powers answer-quality scoring, evals, and (later) enrichment and
            memory. Choosing a new one replaces the current designation immediately.
          </p>
        </div>
      </div>
      <ModelFormDialog
        key={formKey}
        open={formTarget !== null}
        onOpenChange={(o) => !o && setFormTarget(null)}
        model={formTarget === 'create' ? null : formTarget}
      />
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent
          title="Remove model"
          description={`"${removing?.display_name ?? ''}" will be removed from the gateway and every picker.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteModel.isPending}
              onClick={() => {
                if (removing) {
                  deleteModel.mutate(removing.id, { onError: (err) => toast.error(err.message) });
                }
                setRemoving(null);
              }}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
