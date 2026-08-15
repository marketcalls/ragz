import { useState } from 'react';

import type { ModelOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { PROVIDER_CATALOG, type CatalogModel, type CatalogProvider } from './provider-catalog';
import { useCreateModel, type ModelCreate } from './queries';

// ModelCreate's provider_kind union deliberately excludes 'tei' (the backend
// rejects it on create — 'tei' only exists as the seeded built-in row). The
// catalog's 'Builtin'/'FastEmbed' entries carry provider_kind 'tei' and are
// therefore not registerable through this panel.
type CreatableProviderKind = Exclude<CatalogProvider['provider_kind'], 'tei'>;

function isCreatable(provider: CatalogProvider): provider is CatalogProvider & {
  provider_kind: CreatableProviderKind;
} {
  return provider.provider_kind !== 'tei';
}

/** Provider cards the grid can open a panel for — the 'tei' built-in kind is
 * excluded (see isCreatable above). */
export const CREATABLE_PROVIDERS: CatalogProvider[] = PROVIDER_CATALOG.filter(isCreatable);

/** Simple heuristic: a registered model "belongs" to a catalog provider when
 * its provider_kind matches and its litellm_model_name is one of the
 * provider's suggested models. Providers sharing provider_kind "litellm"
 * (most aggregators) can therefore both claim a model with the same name —
 * good enough for a configured-count badge, not used for any security
 * decision. */
function matchesProvider(model: ModelOut, provider: CatalogProvider): boolean {
  return (
    model.provider_kind === provider.provider_kind &&
    provider.models.some((m) => m.litellm_model_name === model.litellm_model_name)
  );
}

export function countConfigured(provider: CatalogProvider, registered: ModelOut[]): number {
  return registered.filter((m) => matchesProvider(m, provider)).length;
}

export function ProviderPanel({
  provider,
  registered,
  onClose,
}: {
  provider: CatalogProvider;
  registered: ModelOut[];
  onClose: () => void;
}) {
  const createModel = useCreateModel();
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showCustom, setShowCustom] = useState(false);
  const [customName, setCustomName] = useState('');
  const [customDisplay, setCustomDisplay] = useState('');
  const [customModality, setCustomModality] = useState<'chat' | 'embedding'>('chat');
  const [embeddingDimension, setEmbeddingDimension] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isCreatable(provider)) return null; // guarded at the call site (CREATABLE_PROVIDERS)

  const isRegistered = (model: CatalogModel): boolean =>
    registered.some(
      (m) => m.provider_kind === provider.provider_kind && m.litellm_model_name === model.litellm_model_name,
    );

  const toggle = (name: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const buildBody = (m: {
    litellm_model_name: string;
    display_name: string;
    modality: 'chat' | 'embedding';
    supports_vision?: boolean;
    supports_reasoning?: boolean;
  }): ModelCreate => {
    const resolvedBaseUrl = provider.base_url ?? (baseUrl.trim() || undefined);
    return {
      litellm_model_name: m.litellm_model_name,
      display_name: m.display_name,
      provider_kind: provider.provider_kind,
      ...(resolvedBaseUrl ? { base_url: resolvedBaseUrl } : {}),
      ...(apiKey ? { api_key: apiKey } : {}),
      modality: m.modality,
      // Backend's ModelCreate validator requires `dimension` when
      // modality === 'embedding' (422 otherwise) and rejects it on chat
      // models -- so it's only ever set here for embedding rows, and
      // handleAddSelected blocks submission before this can carry NaN.
      ...(m.modality === 'embedding' ? { dimension: Number(embeddingDimension) } : {}),
      supports_vision: m.supports_vision ?? false,
      supports_reasoning: m.supports_reasoning ?? false,
      default_reasoning_effort: 'off',
      tools_unreliable: false,
    };
  };

  const hasEmbeddingSelected = provider.models.some(
    (m) => selected.has(m.litellm_model_name) && m.modality === 'embedding',
  );
  const hasEmbeddingCustom = showCustom && customModality === 'embedding';
  const showDimensionInput = hasEmbeddingSelected || hasEmbeddingCustom;

  const isValidDimension = (value: string): boolean => {
    const trimmed = value.trim();
    if (trimmed === '') return false;
    const n = Number(trimmed);
    return Number.isInteger(n) && n > 0;
  };

  const handleAddSelected = async (): Promise<void> => {
    setError(null);
    const bodies: ModelCreate[] = provider.models
      .filter((m) => selected.has(m.litellm_model_name))
      .map(buildBody);
    if (showCustom && customName.trim() && customDisplay.trim()) {
      bodies.push(
        buildBody({
          litellm_model_name: customName.trim(),
          display_name: customDisplay.trim(),
          modality: customModality,
        }),
      );
    }
    if (bodies.length === 0) return;
    if (bodies.some((b) => b.modality === 'embedding') && !isValidDimension(embeddingDimension)) {
      setError('Embedding dimension is required (a positive whole number) to register an embedding model.');
      return;
    }
    setSubmitting(true);
    try {
      for (const body of bodies) {
        // Sequential, not Promise.all: keeps sync-status/gateway registration
        // order deterministic and lets a mid-batch failure stop cleanly.
        await createModel.mutateAsync(body);
      }
      toast(`${bodies.length} model${bodies.length > 1 ? 's' : ''} registered`);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed');
    } finally {
      setSubmitting(false);
    }
  };

  const hasSelection = selected.size > 0 || (showCustom && customName.trim() !== '' && customDisplay.trim() !== '');

  return (
    <div className="space-y-3">
      {provider.needs_api_key ? (
        <div>
          <Label htmlFor="provider-api-key">API key</Label>
          <Input
            id="provider-api-key"
            type="password"
            autoComplete="off"
            placeholder="Write-only — a fingerprint is shown after save"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
      ) : null}
      {provider.needs_base_url ? (
        <div>
          <Label htmlFor="provider-base-url">Base URL</Label>
          <Input
            id="provider-base-url"
            type="url"
            placeholder="http://ollama:11434"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
      ) : null}
      {provider.models.length > 0 ? (
        <div>
          <Label>Suggested models</Label>
          <div className="max-h-64 space-y-1 overflow-y-auto rounded-md border border-line p-1.5">
            {provider.models.map((model) => {
              const already = isRegistered(model);
              return (
                <label
                  key={model.litellm_model_name}
                  className="flex items-center gap-2 rounded-md px-1.5 py-1 text-[13px] text-ink hover:bg-subtle"
                >
                  <input
                    type="checkbox"
                    aria-label={model.display_name}
                    checked={already || selected.has(model.litellm_model_name)}
                    disabled={already}
                    onChange={() => toggle(model.litellm_model_name)}
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                  <span className="flex-1 truncate">{model.display_name}</span>
                  {model.context_tokens ? (
                    <span className="text-[11px] text-muted tabular-nums">
                      {Math.round(model.context_tokens / 1000)}k ctx
                    </span>
                  ) : null}
                  {model.supports_vision ? (
                    <span className="text-[11px] text-accent">vision</span>
                  ) : null}
                  {already ? <span className="text-[11px] text-success">✓ added</span> : null}
                </label>
              );
            })}
          </div>
        </div>
      ) : null}
      <div>
        <button
          type="button"
          aria-expanded={showCustom}
          onClick={() => setShowCustom((v) => !v)}
          className="text-[12px] font-medium text-accent hover:opacity-90"
        >
          {showCustom ? 'Hide custom model' : 'Add custom model'}
        </button>
        {showCustom ? (
          <div className="mt-2 space-y-2 rounded-md border border-line p-2">
            <div>
              <Label htmlFor="custom-model-id">Model id</Label>
              <Input
                id="custom-model-id"
                placeholder="e.g. anthropic/claude-3-5-haiku-20241022"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="custom-model-display">Display name</Label>
              <Input
                id="custom-model-display"
                value={customDisplay}
                onChange={(e) => setCustomDisplay(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="custom-model-modality">Type</Label>
              <NativeSelect
                id="custom-model-modality"
                value={customModality}
                onChange={(e) => setCustomModality(e.target.value as 'chat' | 'embedding')}
              >
                <option value="chat">Chat model</option>
                <option value="embedding">Embedding model</option>
              </NativeSelect>
            </div>
          </div>
        ) : null}
      </div>
      {showDimensionInput ? (
        <div>
          <Label htmlFor="embedding-dimension">
            Embedding dimension (required for embedding models, e.g. 1024 for bge-m3)
          </Label>
          <Input
            id="embedding-dimension"
            type="number"
            min={1}
            step={1}
            value={embeddingDimension}
            onChange={(e) => setEmbeddingDimension(e.target.value)}
          />
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-[12px] text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button
          type="button"
          variant="primary"
          disabled={!hasSelection || submitting}
          onClick={() => void handleAddSelected()}
        >
          Add selected
        </Button>
      </div>
    </div>
  );
}
