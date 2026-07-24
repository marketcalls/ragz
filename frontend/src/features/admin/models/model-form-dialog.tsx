import { useMemo, useState, type FormEvent } from 'react';

import type { CatalogEntryOut, ModelOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import {
  PartialSyncError,
  useCatalog,
  useCreateModel,
  usePatchModel,
  type ModelCreate,
  type ModelPatchInput,
} from './queries';

type ProviderKind = ModelCreate['provider_kind'];

const NEEDS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible'];
const NEEDS_KEY: ProviderKind[] = ['openai', 'openai_compatible', 'litellm'];

/** Sentinel provider value for the manual (non-catalog) path. */
const CUSTOM = '__custom__';

/** Familiar providers surface first, in this order; the rest follow alphabetically. */
const PINNED_PROVIDERS = [
  'openai',
  'anthropic',
  'gemini',
  'groq',
  'mistral',
  'ollama',
  'openrouter',
  'deepseek',
  'xai',
];

/** Catalog provider → registry provider_kind. Everything that is not plain
 * OpenAI or Ollama goes through LiteLLM's native routing ("litellm"), where
 * the catalog name is passed verbatim (it already carries its prefix, e.g.
 * `gemini/gemini-2.5-pro`). Hidden from the user — an implementation detail. */
export function deriveProviderKind(catalogProvider: string): ProviderKind {
  if (catalogProvider === 'openai') return 'openai';
  if (catalogProvider === 'ollama') return 'ollama';
  return 'litellm';
}

/** Suggested display name: last path segment, dashes → spaces, Title Case.
 * `gemini/gemini-2.5-pro` → `Gemini 2.5 Pro`. Always editable by the admin. */
export function prettifyModelName(name: string): string {
  const last = name.split('/').pop() ?? name;
  return last
    .split('-')
    .map((word) => (word ? word[0]!.toUpperCase() + word.slice(1) : word))
    .join(' ');
}

function entryDetail(entry: CatalogEntryOut): string | undefined {
  const parts: string[] = [];
  if (entry.max_input_tokens) parts.push(`${Math.round(entry.max_input_tokens / 1000)}k`);
  if (entry.input_cost_per_1m != null) {
    parts.push(
      `$${entry.input_cost_per_1m.toFixed(2)}/$${(entry.output_cost_per_1m ?? 0).toFixed(2)}`,
    );
  }
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

interface ComboOption {
  value: string;
  label: string;
  detail?: string;
}

/** Type-to-filter combobox: a text input filtering a scrollable option list.
 * Closed, it shows the selected option's label; focusing re-opens with an
 * empty filter so the full list is one click away. */
function Combobox({
  id,
  value,
  options,
  onSelect,
  placeholder,
  disabled = false,
}: {
  id: string;
  value: string;
  options: ComboOption[];
  onSelect: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const selected = options.find((o) => o.value === value);
  const q = query.trim().toLowerCase();
  const filtered = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;

  return (
    <div className="relative">
      <Input
        id={id}
        role="combobox"
        aria-expanded={open}
        autoComplete="off"
        required
        disabled={disabled}
        placeholder={placeholder}
        value={open ? query : (selected?.label ?? '')}
        onFocus={() => {
          setOpen(true);
          setQuery('');
        }}
        onChange={(e) => {
          setOpen(true);
          setQuery(e.target.value);
        }}
        onBlur={() => setOpen(false)}
      />
      {open ? (
        <ul
          role="listbox"
          className="absolute z-50 mt-1 max-h-48 w-full overflow-y-auto rounded-md border border-line bg-raised shadow-md"
        >
          {filtered.map((option) => (
            <li key={option.value}>
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                className="flex w-full items-baseline justify-between gap-2 px-2 py-1.5 text-left text-[13px] hover:bg-bg"
                // Keep focus on the input so blur doesn't close the list
                // before this button's click can land.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onSelect(option.value);
                  setOpen(false);
                }}
              >
                <span className="truncate">{option.label}</span>
                {option.detail ? (
                  <span className="shrink-0 text-[11px] text-muted tabular-nums">
                    {option.detail}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="px-2 py-1.5 text-[13px] text-muted">No matches.</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

export function ModelFormDialog({
  open,
  onOpenChange,
  model = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present → edit an existing model (provider/model id become read-only, key stays write-only-and-blank). Absent → add a new model. */
  model?: ModelOut | null;
}) {
  const isEdit = model != null;
  const create = useCreateModel();
  const patch = usePatchModel();
  // Catalog feeds the add-mode picker only; edit mode never needs it.
  const catalog = useCatalog(open && !isEdit);

  const [displayName, setDisplayName] = useState(model?.display_name ?? '');
  // Add mode: the selected CATALOG provider ('' = none yet, CUSTOM = manual path).
  const [catalogProvider, setCatalogProvider] = useState('');
  // Manual provider_kind, used only on the custom/self-hosted path.
  const [manualKind, setManualKind] = useState<ProviderKind>('openai');
  const [modelId, setModelId] = useState(model?.litellm_model_name ?? '');
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? '');
  const [apiKey, setApiKey] = useState(''); // write-only: always starts blank, even editing
  const [toolsUnreliable, setToolsUnreliable] = useState(model?.tools_unreliable ?? false);
  const [supportsReasoning, setSupportsReasoning] = useState(model?.supports_reasoning ?? false);
  const [defaultEffort, setDefaultEffort] = useState<'off' | 'low' | 'medium' | 'high'>(
    model?.default_reasoning_effort ?? 'off',
  );
  const [supportsVision, setSupportsVision] = useState(model?.supports_vision ?? false);
  const [modality, setModality] = useState<'chat' | 'embedding'>('chat');
  const [dimension, setDimension] = useState('1536');

  const isCustom = catalogProvider === CUSTOM;
  // null until a provider is chosen in add mode — downstream fields stay hidden.
  // The 'tei' branch never actually runs: the seeded row's Edit button is
  // hidden (models-page.tsx's Built-in guard), but ModelOut's provider_kind
  // includes 'tei' so the narrowing must be explicit for ProviderKind (this
  // form's CREATE-path union) to still exclude it.
  const kind: ProviderKind | null = isEdit
    ? model.provider_kind === 'tei'
      ? null
      : model.provider_kind
    : isCustom
      ? manualKind
      : catalogProvider
        ? deriveProviderKind(catalogProvider)
        : null;

  const providerOptions = useMemo((): ComboOption[] => {
    const distinct = [
      ...new Set((catalog.data?.entries ?? []).map((e) => e.provider).filter(Boolean)),
    ];
    const pinned = PINNED_PROVIDERS.filter((p) => distinct.includes(p));
    const rest = distinct
      .filter((p) => !PINNED_PROVIDERS.includes(p))
      .sort((a, b) => a.localeCompare(b));
    return [
      ...[...pinned, ...rest].map((p) => ({ value: p, label: p })),
      { value: CUSTOM, label: 'Custom / self-hosted' },
    ];
  }, [catalog.data]);

  const modelOptions = useMemo((): ComboOption[] => {
    // The API already orders (provider ASC, position DESC); the re-sort keeps
    // newest-first self-evident and robust to any caller reordering.
    return (catalog.data?.entries ?? [])
      .filter((e) => e.provider === catalogProvider)
      .slice()
      .sort((a, b) => b.position - a.position)
      .map((e) => ({ value: e.name, label: e.name, detail: entryDetail(e) }));
  }, [catalog.data, catalogProvider]);

  const pending = isEdit ? patch.isPending : create.isPending;
  const activeError = isEdit
    ? patch.isError
      ? patch.error
      : null
    : create.isError
      ? create.error
      : null;

  const close = (next: boolean): void => {
    if (!next) {
      setDisplayName(model?.display_name ?? '');
      setCatalogProvider('');
      setManualKind('openai');
      setModelId(model?.litellm_model_name ?? '');
      setBaseUrl(model?.base_url ?? '');
      setApiKey(''); // key never lingers in state after close
      setToolsUnreliable(model?.tools_unreliable ?? false);
      setSupportsReasoning(model?.supports_reasoning ?? false);
      setDefaultEffort(model?.default_reasoning_effort ?? 'off');
      setSupportsVision(model?.supports_vision ?? false);
      setModality('chat');
      setDimension('1536');
      create.reset();
      patch.reset();
    }
    onOpenChange(next);
  };

  const pickProvider = (value: string): void => {
    setCatalogProvider(value);
    // A model belongs to one provider — switching invalidates the pick and
    // any display name suggested from it.
    setModelId('');
    setDisplayName('');
    setBaseUrl('');
  };

  const pickModel = (name: string): void => {
    setModelId(name);
    setDisplayName(prettifyModelName(name)); // a suggestion — stays editable
  };

  const handleSettled = {
    onSuccess: () => {
      toast(
        isEdit
          ? 'Model updated — key stored, fingerprint shown in the table'
          : 'Model added — key stored, fingerprint shown in the table',
      );
      close(false);
    },
    onError: (err: Error) => {
      if (err instanceof PartialSyncError) {
        // The local write already succeeded — only the gateway sync failed.
        // Closing here is correct: re-opening would just re-show stale fields.
        toast.error(err.message);
        close(false);
        return;
      }
      // Any other failure: keep the dialog open, inline message below shows it.
    },
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    if (kind === null) return; // no provider chosen yet (required blocks this anyway)

    if (isEdit && model) {
      const body: ModelPatchInput = {};
      if (displayName !== model.display_name) body.display_name = displayName;
      if (NEEDS_BASE_URL.includes(kind) && baseUrl !== (model.base_url ?? '')) {
        body.base_url = baseUrl;
      }
      if (apiKey) body.api_key = apiKey;
      if (toolsUnreliable !== (model.tools_unreliable ?? false)) {
        body.tools_unreliable = toolsUnreliable;
      }
      if (supportsReasoning !== (model.supports_reasoning ?? false)) {
        body.supports_reasoning = supportsReasoning;
      }
      if (defaultEffort !== (model.default_reasoning_effort ?? 'off')) {
        body.default_reasoning_effort = defaultEffort;
      }
      if (supportsVision !== (model.supports_vision ?? false)) {
        body.supports_vision = supportsVision;
      }
      patch.mutate({ modelId: model.id, body }, handleSettled);
      return;
    }

    const body: ModelCreate = {
      display_name: displayName,
      litellm_model_name: modelId, // verbatim catalog name (or manual id)
      provider_kind: kind,
      ...(NEEDS_BASE_URL.includes(kind) && baseUrl ? { base_url: baseUrl } : {}),
      ...(NEEDS_KEY.includes(kind) && apiKey ? { api_key: apiKey } : {}),
      tools_unreliable: toolsUnreliable,
      supports_reasoning: supportsReasoning,
      default_reasoning_effort: defaultEffort,
      supports_vision: supportsVision,
      ...(modality === 'embedding'
        ? { modality: 'embedding' as const, dimension: Number(dimension) }
        : {}),
    };
    create.mutate(body, handleSettled);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={isEdit ? 'Edit model' : 'Add model'}
        description="Synced to the LiteLLM gateway on save."
      >
        <form onSubmit={onSubmit} className="space-y-3">
          {isEdit ? (
            <>
              <div>
                <Label htmlFor="model-provider">Provider</Label>
                <Input id="model-provider" disabled value={model.provider_kind} />
              </div>
              <div>
                <Label htmlFor="model-id">Model id</Label>
                <Input id="model-id" disabled value={modelId} />
              </div>
            </>
          ) : (
            <>
              <div>
                <Label htmlFor="model-provider">Provider</Label>
                <Combobox
                  id="model-provider"
                  value={catalogProvider}
                  options={providerOptions}
                  onSelect={pickProvider}
                  placeholder="Search providers…"
                />
              </div>
              <div>
                <Label htmlFor="model-modality">Type</Label>
                <NativeSelect
                  id="model-modality"
                  value={modality}
                  onChange={(e) => setModality(e.target.value as 'chat' | 'embedding')}
                >
                  <option value="chat">Chat model</option>
                  <option value="embedding">Embedding model</option>
                </NativeSelect>
              </div>
              {isCustom ? (
                <>
                  <div>
                    <Label htmlFor="model-kind">Provider kind</Label>
                    <NativeSelect
                      id="model-kind"
                      value={manualKind}
                      onChange={(e) => setManualKind(e.target.value as ProviderKind)}
                    >
                      <option value="openai">OpenAI</option>
                      <option value="ollama">Ollama</option>
                      <option value="openai_compatible">OpenAI-compatible URL</option>
                    </NativeSelect>
                  </div>
                  <div>
                    <Label htmlFor="model-id">Model id</Label>
                    <Input
                      id="model-id"
                      required
                      placeholder="e.g. gpt-4o-mini"
                      value={modelId}
                      onChange={(e) => setModelId(e.target.value)}
                    />
                  </div>
                </>
              ) : catalogProvider ? (
                <div>
                  <Label htmlFor="model-id">Model</Label>
                  <Combobox
                    id="model-id"
                    value={modelId}
                    options={modelOptions}
                    onSelect={pickModel}
                    placeholder="Search models…"
                  />
                </div>
              ) : null}
            </>
          )}
          {isEdit || isCustom || modelId !== '' ? (
            <div>
              <Label htmlFor="model-display">Display name</Label>
              <Input
                id="model-display"
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>
          ) : null}
          {!isEdit && modality === 'embedding' && (isCustom || modelId !== '') ? (
            <div>
              <Label htmlFor="model-dimension">Vector dimension</Label>
              <Input
                id="model-dimension"
                type="number"
                min={1}
                required
                value={dimension}
                onChange={(e) => setDimension(e.target.value)}
              />
            </div>
          ) : null}
          {kind !== null && NEEDS_BASE_URL.includes(kind) ? (
            <div>
              <Label htmlFor="model-base-url">Base URL</Label>
              <Input
                id="model-base-url"
                required
                type="url"
                placeholder="http://ollama:11434"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
          ) : null}
          {kind !== null && NEEDS_KEY.includes(kind) ? (
            <div>
              <Label htmlFor="model-api-key">API key</Label>
              <Input
                id="model-api-key"
                type="password"
                autoComplete="off"
                placeholder={
                  isEdit
                    ? `Leave blank to keep existing key${model?.key_fingerprint ? ` (${model.key_fingerprint})` : ''}`
                    : 'Write-only — a fingerprint is shown after save'
                }
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
          ) : null}
          {kind !== null && modality === 'chat' ? (
            <label className="flex items-center gap-2 text-[13px] text-secondary">
              <input
                type="checkbox"
                checked={toolsUnreliable}
                onChange={(e) => setToolsUnreliable(e.target.checked)}
                aria-label="Unreliable at native tool calling"
              />
              Unreliable at native tool calling — agent uses the JSON planner
            </label>
          ) : null}
          {kind !== null && modality === 'chat' ? (
            <>
              <label className="flex items-center gap-2 text-[13px] text-secondary">
                <input
                  type="checkbox"
                  checked={supportsReasoning}
                  onChange={(e) => setSupportsReasoning(e.target.checked)}
                  aria-label="Supports reasoning"
                />
                Supports reasoning — lets chat users pick a thinking effort
              </label>
              {supportsReasoning ? (
                <div>
                  <Label htmlFor="model-default-effort">Default reasoning effort</Label>
                  <NativeSelect
                    id="model-default-effort"
                    aria-label="Default reasoning effort"
                    value={defaultEffort}
                    onChange={(e) =>
                      setDefaultEffort(e.target.value as 'off' | 'low' | 'medium' | 'high')
                    }
                  >
                    <option value="off">Off</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </NativeSelect>
                </div>
              ) : null}
              <label className="flex items-center gap-2 text-[13px] text-secondary">
                <input
                  type="checkbox"
                  checked={supportsVision}
                  onChange={(e) => setSupportsVision(e.target.checked)}
                  aria-label="Supports vision"
                />
                Supports vision — lets chat users attach images
              </label>
            </>
          ) : null}
          {activeError && !(activeError instanceof PartialSyncError) ? (
            <p role="alert" className="text-[12px] text-danger">
              {activeError.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {isEdit ? 'Save changes' : 'Add model'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
