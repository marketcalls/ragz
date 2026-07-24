import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

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
  const queryClient = useQueryClient();
  const [locked, setLocked] = useState(false);
  const [pendingModelId, setPendingModelId] = useState<string | null>(null);
  // Fetch unconditionally on mount rather than gating on `pendingModelId`
  // (local state that resets whenever the settings dialog unmounts/remounts
  // on close/reopen): GET /reembed-status already returns the latest job for
  // this workspace unconditionally, or a clean 404 -> null when none has ever
  // run, so there's nothing local state needs to protect here. Without this,
  // a user who closed the dialog mid-job (or right after a failure) and
  // reopened it lost all visibility into that job.
  const status = useReembedStatus(workspaceId);

  const embeddingModels = (models.data ?? []).filter((m) => m.modality === 'embedding');
  const running = status.data != null && status.data.finished_at == null;

  // Once a job genuinely completes (finished_at set, no error), the
  // workspace's embedding_model_id has changed server-side -- refresh the
  // ['workspaces'] cache so `currentModelId` (a prop from the parent, backed
  // by useWorkspaces) picks up the new model. Track the job id already
  // invalidated so this fires exactly once per completed job, not on every
  // subsequent poll tick (the job keeps getting refetched at a slower cadence
  // even once `finished_at` is set, since refetchInterval only stops new
  // polling -- it doesn't unmount the query).
  const invalidatedJobIdRef = useRef<string | null>(null);
  useEffect(() => {
    const job = status.data;
    if (
      job != null &&
      job.finished_at != null &&
      job.error == null &&
      invalidatedJobIdRef.current !== job.id
    ) {
      invalidatedJobIdRef.current = job.id;
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] });
    }
  }, [status.data, queryClient]);

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
          // The status query is already mounted and enabled (Bug 2 fix), so
          // it won't fire again on its own until its next 1.5s poll tick --
          // refetch it explicitly so the newly-started job's progress shows
          // up immediately instead of after a delay.
          void status.refetch();
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
        </div>
      ) : null}
      {/* Independent of `running`: the backend sets `error` and `finished_at`
          together on failure, so the instant a failed job is polled, `running`
          is already false. This must not be nested inside the block above or
          the failure message disappears the moment it becomes true. It also
          shows correctly when the dialog is reopened after a failure, now
          that the status query above fetches unconditionally on mount. */}
      {status.data?.error ? (
        <div className="text-[12px] text-danger" role="alert">
          Re-embed failed: {status.data.error}
        </div>
      ) : null}
    </div>
  );
}
