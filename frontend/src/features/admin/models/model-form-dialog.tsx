import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useCreateModel, type ModelCreate } from './queries';

type ProviderKind = ModelCreate['provider_kind'];

const NEEDS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible'];
const NEEDS_KEY: ProviderKind[] = ['openai', 'openai_compatible'];

export function ModelFormDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateModel();
  const [displayName, setDisplayName] = useState('');
  const [provider, setProvider] = useState<ProviderKind>('openai');
  const [modelId, setModelId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');

  const close = (next: boolean): void => {
    if (!next) {
      setDisplayName('');
      setProvider('openai');
      setModelId('');
      setBaseUrl('');
      setApiKey(''); // key never lingers in state after close
      create.reset();
    }
    onOpenChange(next);
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    const body: ModelCreate = {
      display_name: displayName,
      litellm_model_name: modelId,
      provider_kind: provider,
      ...(NEEDS_BASE_URL.includes(provider) && baseUrl ? { base_url: baseUrl } : {}),
      ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
    };
    create.mutate(body, {
      onSuccess: () => {
        toast('Model added — key stored, fingerprint shown in the table');
        close(false);
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent title="Add model" description="Synced to the LiteLLM gateway on save.">
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
                placeholder="Write-only — a fingerprint is shown after save"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
          ) : null}
          {create.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              {create.error.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>
              Add model
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
