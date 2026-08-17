import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import type { ModelOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { StatusPill } from '@/components/ui/status-pill';

import { ModelFormDialog } from './model-form-dialog';
import { CREATABLE_PROVIDERS, countConfigured, ProviderPanel } from './provider-panel';
import { ProviderIcon } from './provider-icon';
import type { CatalogProvider } from './provider-catalog';
import { useAdminModels } from './queries';
import { RegisteredModelsTable } from './registered-models-table';

export function ModelsPage() {
  const models = useAdminModels();
  // 'create' | null (closed). `formKey` forces a fresh mount on every open so the
  // dialog's fields always start empty instead of stale state from a previous
  // session. Editing/removing an existing row is owned by RegisteredModelsTable.
  const [formTarget, setFormTarget] = useState<'create' | ModelOut | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [providerQuery, setProviderQuery] = useState('');
  const [activeProvider, setActiveProvider] = useState<CatalogProvider | null>(null);

  const filteredProviders = useMemo(() => {
    const q = providerQuery.trim().toLowerCase();
    return q ? CREATABLE_PROVIDERS.filter((p) => p.name.toLowerCase().includes(q)) : CREATABLE_PROVIDERS;
  }, [providerQuery]);

  const openCreate = (): void => {
    setFormTarget('create');
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
          <div className="mb-6">
            <h2 className="mb-2 text-[13px] font-semibold text-ink">Providers</h2>
            <Input
              type="search"
              placeholder="Search providers…"
              aria-label="Search providers"
              value={providerQuery}
              onChange={(e) => setProviderQuery(e.target.value)}
              className="mb-3 max-w-xs"
            />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
              {filteredProviders.map((provider) => {
                const configured = countConfigured(provider, models.data ?? []);
                return (
                  <button
                    key={provider.id}
                    type="button"
                    onClick={() => setActiveProvider(provider)}
                    className="flex flex-col items-start gap-2 rounded-lg border border-line bg-bg p-3 text-left hover:bg-subtle"
                  >
                    <div className="flex w-full items-center justify-between gap-2">
                      <ProviderIcon provider={provider} className="h-8 w-8" />
                      {configured > 0 ? (
                        <StatusPill tone="success">{configured} configured</StatusPill>
                      ) : null}
                    </div>
                    <span className="text-[13px] font-medium text-ink">{provider.name}</span>
                    <div className="flex flex-wrap gap-1">
                      {provider.capabilities.map((c) => (
                        <StatusPill key={c} tone="muted" className="capitalize">
                          {c}
                        </StatusPill>
                      ))}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
          <h2 className="mb-2 text-[13px] font-semibold text-ink">Registered chat models</h2>
          <p className="mb-3 text-[12px] text-muted">
            Embedding models live in{' '}
            <Link to="/admin/settings" className="underline hover:text-ink">
              Settings › Embedding
            </Link>
            , next to the global default that new workspaces inherit.
          </p>
          <RegisteredModelsTable modality="chat" />
        </div>
      </div>
      <ModelFormDialog
        key={formKey}
        open={formTarget !== null}
        onOpenChange={(o) => !o && setFormTarget(null)}
        model={formTarget === 'create' ? null : formTarget}
      />
      <Dialog open={activeProvider !== null} onOpenChange={(o) => !o && setActiveProvider(null)}>
        {activeProvider ? (
          <DialogContent
            title={activeProvider.name}
            description="Enter the credentials once and pick the models to register."
          >
            <ProviderPanel
              provider={activeProvider}
              registered={models.data ?? []}
              onClose={() => setActiveProvider(null)}
            />
          </DialogContent>
        ) : null}
      </Dialog>
    </>
  );
}
