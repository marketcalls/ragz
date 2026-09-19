import { Clock3, FlaskConical, Search, Sparkles, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import type { GoldenQueryOut } from '@/api/types';
import { AssistantMessage } from '@/features/chat/assistant-message';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useDocuments } from '../documents/queries';
import { useModels } from '../models/queries';
import {
  useCompareAnswers,
  useCreateGoldenQuery,
  useDeleteGoldenQuery,
  useGoldenQueries,
} from './evals-queries';

const EXAMPLES = [
  'Why can a high-bandwidth, long-delay TCP path underuse the link unless its receive window is enlarged?',
  'After one frame is lost, how do Go-Back-N and Selective Repeat differ?',
  'Why may BGP deliberately select an AS path that is not the shortest available route?',
] as const;

function ms(value: number): string {
  return `${value.toFixed(1)} ms`;
}

type ComparisonSnapshot = {
  question: string;
  modelLabel: string;
};

// Task 10 (eval harness, design §6) mount point: admin-only CRUD for
// GoldenQuery fixtures, sibling to (not nested in) MetadataFieldsSection —
// same immediate-mutation pattern, no separate "Save" step. `documents`
// isn't threaded in as a prop from WorkspaceSettingsDialog's mount point
// (DocumentsPage already has its own useDocuments(workspaceId, folderId)
// call, but piping that through two more prop layers is strictly more
// plumbing than this component fetching its own unfiltered copy —
// passing folderId: null here means it only shares DocumentsPage's cache
// entry when DocumentsPage also has no folder selected; TanStack Query
// dedupes/caches by the full ['documents', workspaceId, folderId] key).
export function EvalsSection({
  workspaceId,
  defaultModelId = null,
  canRead = true,
  canManage = true,
  canRun = true,
  canListDocuments = true,
  canReadModels = true,
}: {
  workspaceId: string;
  defaultModelId?: string | null;
  canRead?: boolean;
  canManage?: boolean;
  canRun?: boolean;
  canListDocuments?: boolean;
  canReadModels?: boolean;
}) {
  const documents = useDocuments(canManage && canListDocuments ? workspaceId : null, null);
  const models = useModels(canRun && canReadModels);
  const queries = useGoldenQueries(canRead ? workspaceId : null);
  const createQuery = useCreateGoldenQuery(workspaceId);
  const deleteQuery = useDeleteGoldenQuery(workspaceId);
  const compare = useCompareAnswers(canRun ? workspaceId : null);
  const [comparisonQuestion, setComparisonQuestion] = useState('');
  const [modelId, setModelId] = useState<string | null>(defaultModelId);
  const [question, setQuestion] = useState('');
  const [expected, setExpected] = useState<Set<string>>(new Set());
  const [removing, setRemoving] = useState<GoldenQueryOut | null>(null);
  const [comparisonSnapshot, setComparisonSnapshot] = useState<ComparisonSnapshot | null>(null);
  const availableModels = models.data ?? [];
  const effectiveModelId = canReadModels
    ? availableModels.some((model) => model.id === modelId)
      ? modelId
      : (availableModels.find((model) => model.id === defaultModelId)?.id ??
        availableModels[0]?.id ??
        null)
    : defaultModelId;
  const selectedModel = availableModels.find((model) => model.id === effectiveModelId);

  const toggleExpected = (id: string): void => {
    setExpected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    if (!question.trim()) return;
    createQuery.mutate(
      { question: question.trim(), expected_document_ids: [...expected] },
      {
        onSuccess: () => {
          setQuestion('');
          setExpected(new Set());
        },
        onError: (err) => toast.error(err.message),
      },
    );
  };

  return (
    <div className="space-y-6">
      {canRun ? (
        <section className="space-y-3" aria-labelledby="comparison-heading">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="mb-1 flex items-center gap-2">
                <FlaskConical className="h-4 w-4 text-secondary" aria-hidden />
                <h3 id="comparison-heading" className="text-[13px] font-semibold text-ink">
                  Retrieval A/B lab
                </h3>
              </div>
              <p className="max-w-3xl text-[12px] text-secondary">
                Generate two grounded one-turn answers with the same model and workspace. The saved
                multi-query setting is not changed; chat history, agents, and web search are
                excluded.
              </p>
            </div>
            <span className="shrink-0 rounded-md border border-line bg-subtle px-2 py-1 text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
              Costs 2 answers
            </span>
          </div>

          <div className="grid gap-3 rounded-lg border border-line bg-raised p-3 lg:grid-cols-[minmax(0,1fr)_220px_auto] lg:items-end">
            <div className="space-y-1">
              <Label htmlFor="comparison-question">Comparison question</Label>
              <textarea
                id="comparison-question"
                value={comparisonQuestion}
                onChange={(event) => setComparisonQuestion(event.target.value)}
                rows={3}
                maxLength={2000}
                placeholder="Ask one question against the indexed documents…"
                className="w-full resize-y rounded-md border border-line bg-bg px-3 py-2 text-[13px] text-ink placeholder:text-muted focus:border-line-strong"
              />
            </div>
            {canReadModels ? (
              <div className="space-y-1">
                <Label htmlFor="comparison-model">Answer model</Label>
                <NativeSelect
                  id="comparison-model"
                  value={effectiveModelId ?? ''}
                  onChange={(event) => setModelId(event.target.value)}
                  disabled={availableModels.length === 0}
                >
                  {availableModels.length > 0 ? (
                    availableModels.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.display_name}
                      </option>
                    ))
                  ) : (
                    <option value="">No enabled chat models available</option>
                  )}
                </NativeSelect>
              </div>
            ) : (
              <div className="space-y-1">
                <span className="text-[12px] font-medium text-secondary">Answer model</span>
                <p className="rounded-md border border-line bg-bg px-3 py-2 text-[13px] text-muted">
                  Workspace default
                </p>
              </div>
            )}
            <Button
              variant="primary"
              className="h-8"
              disabled={
                !comparisonQuestion.trim() || effectiveModelId === null || compare.isPending
              }
              onClick={() => {
                const submittedQuestion = comparisonQuestion.trim();
                const submittedModelId = effectiveModelId;
                if (submittedModelId === null) return;
                compare.mutate(
                  {
                    question: submittedQuestion,
                    model_id: submittedModelId,
                  },
                  {
                    onSuccess: () =>
                      setComparisonSnapshot({
                        question: submittedQuestion,
                        modelLabel: selectedModel?.display_name ?? 'Workspace default',
                      }),
                    onError: (error) => toast.error(error.message),
                  },
                );
              }}
            >
              {compare.isPending ? 'Comparing…' : 'Compare answers'}
            </Button>
          </div>

          <div className="flex flex-wrap gap-1.5" aria-label="Example networking questions">
            {EXAMPLES.map((example, index) => (
              <button
                key={example}
                type="button"
                onClick={() => setComparisonQuestion(example)}
                className="rounded-md border border-line-faint bg-subtle px-2 py-1 text-left text-[11px] text-secondary transition-colors hover:border-line hover:text-ink"
              >
                Example {index + 1}
              </button>
            ))}
          </div>

          {compare.isPending ? (
            <div className="rounded-lg border border-line bg-raised py-10">
              <Spinner label="Running both retrieval paths and generating answers…" />
            </div>
          ) : null}

          {compare.data && comparisonSnapshot ? (
            <div className="grid gap-3 lg:grid-cols-[minmax(220px,0.65fr)_repeat(2,minmax(0,1fr))]">
              <aside className="rounded-lg border border-line bg-subtle p-4">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
                  Fixed input
                </p>
                <p className="text-[14px] font-medium leading-relaxed text-ink">
                  {comparisonSnapshot.question}
                </p>
                <dl className="mt-5 space-y-2 border-t border-line pt-3 text-[12px]">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Workspace</dt>
                    <dd className="font-medium text-secondary">Same</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">Answer model</dt>
                    <dd className="truncate font-medium text-secondary">
                      {comparisonSnapshot.modelLabel}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-muted">History / web / agent</dt>
                    <dd className="font-medium text-secondary">Off</dd>
                  </div>
                </dl>
              </aside>

              {compare.data.variants.map((variant) => {
                const isMulti = variant.mode === 'multi';
                return (
                  <article
                    key={variant.mode}
                    className={`min-w-0 rounded-lg border border-line bg-raised p-4 shadow-sm ${
                      isMulti ? 'border-t-2 border-t-success' : 'border-t-2 border-t-line-strong'
                    }`}
                  >
                    <div className="mb-4 flex items-start justify-between gap-3 border-b border-line-faint pb-3">
                      <div>
                        <div className="flex items-center gap-2">
                          {isMulti ? (
                            <Sparkles className="h-4 w-4 text-success" aria-hidden />
                          ) : (
                            <Search className="h-4 w-4 text-secondary" aria-hidden />
                          )}
                          <h4 className="text-[14px] font-semibold text-ink">
                            {isMulti ? 'Multi-query' : 'Single query'}
                          </h4>
                        </div>
                        <p className="mt-1 text-[11px] text-muted">
                          {variant.query_count} retrieval{' '}
                          {variant.query_count === 1 ? 'query' : 'queries'}
                        </p>
                      </div>
                      <span className="inline-flex items-center gap-1 text-[11px] tabular-nums text-muted">
                        <Clock3 className="h-3 w-3" aria-hidden />
                        {ms(variant.total_ms)} total
                      </span>
                    </div>

                    <AssistantMessage
                      content={variant.answer}
                      sources={variant.sources.map((source) => ({
                        marker: source.marker,
                        document_id: source.document_id,
                        filename: source.filename,
                        page: source.page,
                        snippet: source.snippet,
                        section: source.section,
                        version: source.version,
                      }))}
                      noAnswer={variant.no_answer}
                    />

                    <div className="mt-4 grid grid-cols-3 gap-2 border-t border-line-faint pt-3 text-[11px] tabular-nums">
                      <div>
                        <p className="text-muted">Retrieval</p>
                        <p className="mt-0.5 font-medium text-secondary">
                          {ms(variant.retrieval_ms)}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted">Answer</p>
                        <p className="mt-0.5 font-medium text-secondary">
                          {ms(variant.generation_ms)}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted">Tokens</p>
                        <p className="mt-0.5 font-medium text-secondary">
                          {(variant.prompt_tokens + variant.completion_tokens).toLocaleString()}
                        </p>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : null}
        </section>
      ) : null}

      {canRead || canManage ? (
        <section className="space-y-3 border-t border-line pt-5">
          <h3 className="text-[13px] font-semibold text-ink">Golden queries</h3>
          <p className="text-[12px] text-secondary">
            Questions with known-good documents. The eval runner checks retrieval hit-rate, citation
            precision, and (with a utility model designated) answer faithfulness against these.
          </p>
          {canManage ? (
            <form onSubmit={onSubmit} className="space-y-2">
              <div className="space-y-1">
                <Label htmlFor="gq-question">Question</Label>
                <textarea
                  id="gq-question"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  rows={2}
                  maxLength={2000}
                  placeholder="e.g. Where is the muster point?"
                  className="w-full rounded-md border border-line bg-raised px-3 py-2 text-[13px] text-ink placeholder:text-muted"
                />
              </div>
              <div className="flex flex-wrap gap-2">
                {(documents.data ?? []).map((d) => (
                  <label key={d.id} className="flex items-center gap-1 text-[12px] text-secondary">
                    <input
                      type="checkbox"
                      checked={expected.has(d.id)}
                      onChange={() => toggleExpected(d.id)}
                    />
                    {d.filename}
                  </label>
                ))}
              </div>
              {!canListDocuments ? (
                <p className="text-[12px] text-muted">
                  Document selection is hidden because this role cannot list workspace documents.
                </p>
              ) : null}
              <Button type="submit" size="sm" disabled={createQuery.isPending}>
                Add golden query
              </Button>
            </form>
          ) : null}
          {canRead && queries.isPending ? <Spinner label="Loading golden queries…" /> : null}
          {canRead ? (
            <ul className="space-y-1">
              {(queries.data ?? []).map((q) => (
                <li key={q.id} className="flex items-center justify-between text-[13px]">
                  <span className="truncate">{q.question}</span>
                  {canManage ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete golden query: ${q.question}`}
                      onClick={() => setRemoving(q)}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  ) : null}
                </li>
              ))}
              {queries.data?.length === 0 ? (
                <li className="text-[13px] text-muted">No golden queries yet.</li>
              ) : null}
            </ul>
          ) : null}
          {canManage ? (
            <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
              <DialogContent
                title="Delete golden query"
                description={`"${removing?.question ?? ''}" will be removed.`}
              >
                <DialogFooter>
                  <Button onClick={() => setRemoving(null)}>Cancel</Button>
                  <Button
                    variant="danger"
                    disabled={deleteQuery.isPending}
                    onClick={() => {
                      if (removing) {
                        deleteQuery.mutate(removing.id, {
                          onError: (err) => toast.error(err.message),
                        });
                      }
                      setRemoving(null);
                    }}
                  >
                    Delete
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
