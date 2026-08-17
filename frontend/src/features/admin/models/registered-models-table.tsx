import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';

import type { CatalogEntryOut, ModelOut } from '@/api/types';
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

/** The registered-model registry for ONE modality, with its own edit/remove
 * dialogs. Extracted from ModelsPage so the embedding registry can live on
 * Admin > Settings next to the global default that selects from it, while the
 * chat registry stays on Admin > Models. Both used to be one table behind a
 * chat/embedding tab switch. */
export function RegisteredModelsTable({
  modality,
  showAdd = false,
}: {
  modality: 'chat' | 'embedding';
  /** Renders an "Add …" button. The Models page has its own provider-grid
   * create flow, so only the Settings-hosted embedding table needs this. */
  showAdd?: boolean;
}) {
  const models = useAdminModels();
  const catalog = useCatalog();
  const patchModel = usePatchModel();
  const deleteModel = useDeleteModel();
  // 'create' | a specific model being edited | null (closed). `formKey` forces a
  // fresh mount on every open so fields start from the current target's data.
  const [formTarget, setFormTarget] = useState<'create' | ModelOut | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [removing, setRemoving] = useState<ModelOut | null>(null);

  const catalogByName = useMemo(
    () => new Map((catalog.data?.entries ?? []).map((e): [string, CatalogEntryOut] => [e.name, e])),
    [catalog.data],
  );

  const rows = (models.data ?? []).filter((model) => model.modality === modality);
  const isEmbedding = modality === 'embedding';

  return (
    <>
      {showAdd ? (
        <div className="mb-2">
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setFormTarget('create');
              setFormKey((k) => k + 1);
            }}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add embedding model
          </Button>
        </div>
      ) : null}
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
              {/* Dimension is meaningful only for embedding models, and is
                  immutable after creation (see ModelUpdate) — surfacing it
                  here is the only way to spot a wrong value, which otherwise
                  stays invisible until ingestion fails. */}
              {isEmbedding ? <TH>Dimension</TH> : null}
              <TH>Model id</TH>
              <TH>Key</TH>
              <TH>Gateway</TH>
              <TH>Enabled</TH>
              <TH>Utility</TH>
              <TH />
            </TR>
          </THead>
          <TBody>
            {rows.map((model) => {
              const catalogEntry = catalogByName.get(model.litellm_model_name);
              return (
                <TR key={model.id}>
                  <TD className="font-medium">{model.display_name}</TD>
                  <TD className="text-secondary">{model.provider_kind}</TD>
                  <TD className="capitalize text-secondary">{model.modality}</TD>
                  {isEmbedding ? (
                    <TD className="font-mono text-[12px] tabular-nums text-secondary">
                      {model.dimension ?? '—'}
                    </TD>
                  ) : null}
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
                    <StatusPill tone={syncTone(model.sync_status)}>{model.sync_status}</StatusPill>
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
                      // Scoped per modality: the two tables render on different
                      // pages, but a shared name would still couple them if they
                      // ever appeared together.
                      name={`utility-model-${modality}`}
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
                          onClick={() => {
                            setFormTarget(model);
                            setFormKey((k) => k + 1);
                          }}
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
        The utility model powers answer-quality scoring, evals, and (later) enrichment and memory.
        Choosing a new one replaces the current designation immediately.
      </p>
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
