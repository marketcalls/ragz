import { useState, type FormEvent } from 'react';

import type { WorkspaceOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/toaster';

import { usePatchWorkspace } from './queries';

export function WorkspaceSettingsDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: WorkspaceOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const patch = usePatchWorkspace();
  const [topK, setTopK] = useState(String(workspace.top_k));
  const [minScore, setMinScore] = useState(String(workspace.min_score));
  const [rerank, setRerank] = useState(workspace.rerank_enabled);
  const [override, setOverride] = useState(workspace.system_prompt_override ?? '');

  const submit = (e: FormEvent): void => {
    e.preventDefault();
    patch.mutate(
      {
        id: workspace.id,
        top_k: Number(topK),
        min_score: Number(minScore),
        rerank_enabled: rerank,
        system_prompt_override: override.trim() === '' ? null : override,
      },
      {
        onSuccess: () => {
          toast('Workspace retrieval settings saved');
          onOpenChange(false);
        },
        onError: (err: Error) => toast.error(err.message),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Retrieval settings — ${workspace.name}`}
        description="Tuning applies to every chat and search in this workspace."
      >
        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="ws-top-k">Sources per query (top_k)</Label>
              <Input
                id="ws-top-k"
                type="number"
                min={1}
                max={50}
                required
                value={topK}
                onChange={(e) => setTopK(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="ws-min-score">Confidence threshold</Label>
              <Input
                id="ws-min-score"
                type="number"
                min={0}
                max={1}
                step={0.05}
                required
                value={minScore}
                onChange={(e) => setMinScore(e.target.value)}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-[13px] text-secondary">
            <input
              type="checkbox"
              checked={rerank}
              onChange={(e) => setRerank(e.target.checked)}
              aria-label="Rerank with cross-encoder"
            />
            Rerank with cross-encoder
          </label>
          <p className="text-[12px] text-muted">
            With reranking on, the confidence threshold reads the reranker&apos;s 0–1 relevance
            score instead of cosine similarity — recheck it after toggling.
          </p>
          <div className="space-y-1">
            <Label htmlFor="ws-prompt-override">System prompt additions</Label>
            <textarea
              id="ws-prompt-override"
              value={override}
              onChange={(e) => setOverride(e.target.value)}
              rows={4}
              maxLength={8000}
              placeholder="Optional instructions appended to the base system prompt (tone, format, persona). Leave empty to clear."
              className="w-full rounded-md border border-line bg-raised px-3 py-2 text-[13px] text-ink placeholder:text-muted"
            />
          </div>
          <DialogFooter>
            <Button type="button" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={patch.isPending}>
              Save settings
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
