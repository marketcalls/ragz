import { useState, type FormEvent } from 'react';

import type { ModelOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import {
  PartialSyncError,
  useCreateModel,
  usePatchModel,
  type ModelCreate,
  type ModelPatchInput,
} from './queries';

type ProviderKind = ModelCreate['provider_kind'];

const NEEDS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible'];
const NEEDS_KEY: ProviderKind[] = ['openai', 'openai_compatible'];

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

  const [displayName, setDisplayName] = useState(model?.display_name ?? '');
  const [provider, setProvider] = useState<ProviderKind>(model?.provider_kind ?? 'openai');
  const [modelId, setModelId] = useState(model?.litellm_model_name ?? '');
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? '');
  const [apiKey, setApiKey] = useState(''); // write-only: always starts blank, even editing

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
      setProvider(model?.provider_kind ?? 'openai');
      setModelId(model?.litellm_model_name ?? '');
      setBaseUrl(model?.base_url ?? '');
      setApiKey(''); // key never lingers in state after close
      create.reset();
      patch.reset();
    }
    onOpenChange(next);
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

    if (isEdit && model) {
      const body: ModelPatchInput = {};
      if (displayName !== model.display_name) body.display_name = displayName;
      if (NEEDS_BASE_URL.includes(provider) && baseUrl !== (model.base_url ?? '')) {
        body.base_url = baseUrl;
      }
      if (apiKey) body.api_key = apiKey;
      patch.mutate({ modelId: model.id, body }, handleSettled);
      return;
    }

    const body: ModelCreate = {
      display_name: displayName,
      litellm_model_name: modelId,
      provider_kind: provider,
      ...(NEEDS_BASE_URL.includes(provider) && baseUrl ? { base_url: baseUrl } : {}),
      ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
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
          <div>
            <Label htmlFor="model-display">Display name</Label>
            <Input
              id="model-display"
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="model-provider">Provider</Label>
            <NativeSelect
              id="model-provider"
              value={provider}
              disabled={isEdit}
              onChange={(e) => setProvider(e.target.value as ProviderKind)}
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
              disabled={isEdit}
              placeholder="e.g. gpt-4o-mini"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
            />
          </div>
          {NEEDS_BASE_URL.includes(provider) ? (
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
          {NEEDS_KEY.includes(provider) ? (
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
