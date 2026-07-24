import { Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import type { GoldenQueryOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useDocuments } from '../documents/queries';
import { useCreateGoldenQuery, useDeleteGoldenQuery, useGoldenQueries } from './evals-queries';

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
export function EvalsSection({ workspaceId }: { workspaceId: string }) {
  const documents = useDocuments(workspaceId, null);
  const queries = useGoldenQueries(workspaceId);
  const createQuery = useCreateGoldenQuery(workspaceId);
  const deleteQuery = useDeleteGoldenQuery(workspaceId);
  const [question, setQuestion] = useState('');
  const [expected, setExpected] = useState<Set<string>>(new Set());
  const [removing, setRemoving] = useState<GoldenQueryOut | null>(null);

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
    <div className="space-y-3">
      <h3 className="text-[13px] font-semibold text-ink">Golden queries</h3>
      <p className="text-[12px] text-secondary">
        Questions with known-good documents. The eval runner checks retrieval hit-rate, citation
        precision, and (with a utility model designated) answer faithfulness against these.
      </p>
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
        <Button type="submit" size="sm" disabled={createQuery.isPending}>
          Add golden query
        </Button>
      </form>
      {queries.isPending ? <Spinner label="Loading golden queries…" /> : null}
      <ul className="space-y-1">
        {(queries.data ?? []).map((q) => (
          <li key={q.id} className="flex items-center justify-between text-[13px]">
            <span className="truncate">{q.question}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete golden query: ${q.question}`}
              onClick={() => setRemoving(q)}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </li>
        ))}
        {queries.data?.length === 0 ? (
          <li className="text-[13px] text-muted">No golden queries yet.</li>
        ) : null}
      </ul>
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
                  deleteQuery.mutate(removing.id, { onError: (err) => toast.error(err.message) });
                }
                setRemoving(null);
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
