import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useAdminModels } from '../admin/models/queries';
import {
  EmbeddingModelLockedError,
  usePatchEmbeddingModel,
  useReembedStatus,
  useStartReembed,
} from './queries';

export function EmbeddingModelSection({
  workspaceId,
  currentModelId,
}: {
  workspaceId: string;
  currentModelId: string;
}) {
  const models = useAdminModels();
  const patchModel = usePatchEmbeddingModel();
  const startReembed = useStartReembed();
  const [locked, setLocked] = useState(false);
  const [pendingModelId, setPendingModelId] = useState<string | null>(null);
  const status = useReembedStatus(workspaceId, pendingModelId !== null);

  const embeddingModels = (models.data ?? []).filter((m) => m.modality === 'embedding');
  const running = status.data != null && status.data.finished_at == null;

  const onChange = (modelId: string): void => {
    if (modelId === currentModelId) return;
    patchModel.mutate(
      { id: workspaceId, embedding_model_id: modelId },
      {
        onSuccess: () => toast('Embedding model updated'),
        onError: (err: Error) => {
          if (err instanceof EmbeddingModelLockedError) {
            setLocked(true);
            setPendingModelId(modelId);
            return;
          }
          toast.error(err.message);
        },
      },
    );
  };

  const confirmReembed = (): void => {
    if (!pendingModelId) return;
    startReembed.mutate(
      { id: workspaceId, new_embedding_model_id: pendingModelId },
      {
        onSuccess: () => {
          toast('Re-embed started — this workspace will use the new model once it completes');
          setLocked(false);
        },
        onError: (err: Error) => toast.error(err.message),
      },
    );
  };

  return (
    <div className="space-y-2">
      <Label htmlFor="ws-embedding-model">Embedding model</Label>
      <NativeSelect
        id="ws-embedding-model"
        value={currentModelId}
        disabled={running}
        onChange={(e) => onChange(e.target.value)}
      >
        {embeddingModels.map((m) => (
          <option key={m.id} value={m.id}>
            {m.display_name}
          </option>
        ))}
      </NativeSelect>
      {locked ? (
        <div className="rounded-md border border-line bg-subtle p-2 text-[12px]">
          This workspace already has indexed documents — switching requires re-embedding all of
          them into the new model.
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={() => setLocked(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmReembed} disabled={startReembed.isPending}>
              Re-embed now
            </Button>
          </div>
        </div>
      ) : null}
      {running && status.data ? (
        <div className="text-[12px] text-secondary">
          Re-embedding: {status.data.documents_done} / {status.data.documents_total} documents
          {status.data.error ? (
            <span className="text-danger"> — failed: {status.data.error}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
