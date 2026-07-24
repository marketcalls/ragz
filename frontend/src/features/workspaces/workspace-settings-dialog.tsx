import { useState, type FormEvent } from 'react';

import type { WorkspaceOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/toaster';

import { EmbeddingModelSection } from './embedding-model-section';
import { EvalsSection } from './evals-section';
import { MetadataFieldsSection } from './metadata-fields-section';
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
  const [fallback, setFallback] = useState<'general_knowledge' | 'decline'>(
    workspace.fallback_policy as 'general_knowledge' | 'decline',
  );
  const [webSearch, setWebSearch] = useState(workspace.web_search_enabled);
  const [strictMode, setStrictMode] = useState(workspace.strict_mode);
  const [enrichment, setEnrichment] = useState(workspace.enrichment_enabled);
  // J-C15: no shared Tabs primitive exists yet — this local button-group
  // strip matches dashboard-page.tsx's RANGES day-picker style.
  const [tab, setTab] = useState<'settings' | 'evals'>('settings');

  const submit = (e: FormEvent): void => {
    e.preventDefault();

    // Only send fields that actually changed from the snapshot this dialog opened
    // with — the backend's model_fields_set partial-update contract treats every
    // included field as an explicit write, so sending unchanged fields defeats
    // concurrent-admin safety and adds audit noise.
    const changes: {
      top_k?: number;
      min_score?: number;
      rerank_enabled?: boolean;
      system_prompt_override?: string | null;
      fallback_policy?: 'general_knowledge' | 'decline';
      web_search_enabled?: boolean;
      strict_mode?: boolean;
      enrichment_enabled?: boolean;
    } = {};
    const nextTopK = Number(topK);
    const nextMinScore = Number(minScore);
    const nextOverride = override.trim() === '' ? null : override;
    if (nextTopK !== workspace.top_k) changes.top_k = nextTopK;
    if (nextMinScore !== workspace.min_score) changes.min_score = nextMinScore;
    if (rerank !== workspace.rerank_enabled) changes.rerank_enabled = rerank;
    if (nextOverride !== workspace.system_prompt_override) {
      changes.system_prompt_override = nextOverride;
    }
    if (fallback !== workspace.fallback_policy) changes.fallback_policy = fallback;
    if (webSearch !== workspace.web_search_enabled) changes.web_search_enabled = webSearch;
    if (strictMode !== workspace.strict_mode) changes.strict_mode = strictMode;
    if (enrichment !== workspace.enrichment_enabled) changes.enrichment_enabled = enrichment;

    if (Object.keys(changes).length === 0) {
      onOpenChange(false);
      return;
    }

    patch.mutate(
      { id: workspace.id, ...changes },
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
        className="max-w-lg"
      >
        <div className="mb-3 flex gap-1">
          {(['settings', 'evals'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`rounded-md px-2 py-1 text-xs capitalize ${
                tab === t ? 'bg-subtle text-ink' : 'text-secondary'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        {tab === 'settings' ? (
          <>
            <div className="mb-4 border-b border-line pb-4">
              <EmbeddingModelSection
                workspaceId={workspace.id}
                currentModelId={workspace.embedding_model_id}
              />
            </div>
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
                    step="any"
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
                <Label htmlFor="ws-fallback">If retrieval finds nothing</Label>
                <select
                  id="ws-fallback"
                  value={fallback}
                  onChange={(e) => setFallback(e.target.value as 'general_knowledge' | 'decline')}
                  className="w-full rounded-md border border-line bg-raised px-3 py-2 text-[13px] text-ink"
                >
                  <option value="general_knowledge">
                    Answer from general knowledge (labeled, no citations)
                  </option>
                  <option value="decline">Decline to answer (compliance mode)</option>
                </select>
              </div>
              {fallback === 'decline' ? null : (
                <label className="flex items-center gap-2 text-[13px] text-secondary">
                  <input
                    type="checkbox"
                    checked={webSearch}
                    onChange={(e) => setWebSearch(e.target.checked)}
                    aria-label="Allow web search"
                  />
                  Allow web search (Tavily) — answers may cite public web pages
                </label>
              )}
              <label className="flex items-center gap-2 text-[13px] text-secondary">
                <input
                  type="checkbox"
                  checked={strictMode}
                  onChange={(e) => setStrictMode(e.target.checked)}
                  aria-label="Strict mode"
                />
                Strict mode — validate every answer before it streams, one retry on failure
              </label>
              <label className="flex items-center gap-2 text-[13px] text-secondary">
                <input
                  type="checkbox"
                  checked={enrichment}
                  onChange={(e) => setEnrichment(e.target.checked)}
                  aria-label="Enable search-recall enrichment"
                />
                Enrich chunks for better search recall (uses the utility model) — turning this
                off stops new enrichment but does not remove enrichment already applied to
                existing documents
              </label>
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
            <div className="mt-6 border-t border-line pt-4">
              <MetadataFieldsSection workspaceId={workspace.id} />
            </div>
          </>
        ) : (
          <EvalsSection workspaceId={workspace.id} />
        )}
      </DialogContent>
    </Dialog>
  );
}
