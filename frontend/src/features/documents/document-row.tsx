import { Check, Lock, Pin, RotateCw, Tags, Trash2, Undo2 } from 'lucide-react';
import { useState } from 'react';

import type { DocumentOut, MetadataFieldOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { StatusPill } from '@/components/ui/status-pill';
import { TD, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { useClaims } from '@/lib/use-claims';

import { AclDialog } from './acl-dialog';
import { MetadataDialog } from './metadata-dialog';
import { useReindexDocument, useSetApproved } from './queries';
import { formatBytes, statusPresentation } from './status';

export function DocumentRow({
  doc,
  workspaceId,
  fields = [],
  dimmed = false,
  onDelete,
  deleting,
  onTogglePin,
  pinning,
}: {
  doc: DocumentOut;
  workspaceId: string | null;
  // Workspace metadata schema (DOC-6/Task 11) — defaults to [] so existing
  // callers/tests that don't care about metadata don't need to pass it.
  fields?: MetadataFieldOut[];
  dimmed?: boolean;
  onDelete: () => void;
  deleting: boolean;
  onTogglePin: () => void;
  pinning: boolean;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [aclOpen, setAclOpen] = useState(false);
  const [metadataOpen, setMetadataOpen] = useState(false);
  const claims = useClaims();
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';
  const { tone, label } = statusPresentation(doc);
  const restricted = Boolean(doc.acl_group_ids && doc.acl_group_ids.length > 0);
  const setApproved = useSetApproved(workspaceId);
  const reindex = useReindexDocument(workspaceId);
  // Reindex re-runs chunk->embed for the document. Only meaningful from a
  // settled state (mirrors the backend's 409 guard): a document that is
  // already indexed (refresh) or failed (retry).
  const canReindex = doc.status === 'indexed' || doc.status === 'failed';
  return (
    <TR className={dimmed ? 'opacity-60' : undefined}>
      <TD className="max-w-[320px] truncate font-medium">
        {doc.filename}
        <span className="ml-1.5 font-normal text-muted">v{doc.version}</span>
      </TD>
      <TD className="text-secondary">{formatBytes(doc.size_bytes)}</TD>
      <TD className="text-secondary">{doc.page_count ?? '—'}</TD>
      <TD>
        <div className="flex items-center gap-1.5">
          {doc.status === 'failed' ? (
            <Popover>
              <PopoverTrigger asChild>
                <button type="button" aria-label="Show failure reason">
                  <StatusPill tone={tone}>{label}</StatusPill>
                </button>
              </PopoverTrigger>
              <PopoverContent>
                <p className="font-medium text-danger">Ingestion failed</p>
                <p className="mt-1 text-secondary">{doc.error ?? 'Unknown error'}</p>
              </PopoverContent>
            </Popover>
          ) : (
            <StatusPill tone={tone}>{label}</StatusPill>
          )}
          {restricted ? <StatusPill tone="warning">restricted</StatusPill> : null}
          {doc.approved ? <StatusPill tone="success">Approved</StatusPill> : null}
        </div>
      </TD>
      <TD className="text-muted">{new Date(doc.created_at).toLocaleDateString()}</TD>
      <TD className="text-right">
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Edit metadata for ${doc.filename}`}
          title="Edit metadata"
          onClick={() => setMetadataOpen(true)}
        >
          <Tags className="h-4 w-4" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={doc.pinned ? `Unpin ${doc.filename}` : `Pin ${doc.filename}`}
          title="Pinned documents are always included as sources in chat"
          disabled={pinning || doc.status !== 'indexed'}
          onClick={onTogglePin}
        >
          <Pin
            className={doc.pinned ? 'h-4 w-4 fill-current text-accent' : 'h-4 w-4'}
            aria-hidden
          />
        </Button>
        {canReindex ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Reindex ${doc.filename}`}
            title="Re-run indexing for this document"
            disabled={reindex.isPending}
            onClick={() =>
              reindex.mutate(doc.id, {
                onSuccess: () => toast.success('Reindexing…'),
                onError: (err) => toast.error(err.message),
              })
            }
          >
            <RotateCw className="h-4 w-4" aria-hidden />
          </Button>
        ) : null}
        {isAdmin ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Manage access for ${doc.filename}`}
            title="Manage document access"
            onClick={() => setAclOpen(true)}
          >
            <Lock className={restricted ? 'h-4 w-4 text-accent' : 'h-4 w-4'} aria-hidden />
          </Button>
        ) : null}
        {isAdmin ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={doc.approved ? `Unapprove ${doc.filename}` : `Approve ${doc.filename}`}
            title={doc.approved ? 'Revoke approval' : 'Mark approved for retrieval'}
            disabled={setApproved.isPending}
            onClick={() =>
              setApproved.mutate(
                { documentId: doc.id, approved: !doc.approved },
                { onError: (err) => toast.error(err.message) },
              )
            }
          >
            {doc.approved ? (
              <Undo2 className="h-4 w-4" aria-hidden />
            ) : (
              <Check className="h-4 w-4" aria-hidden />
            )}
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete ${doc.filename}`}
          onClick={() => setConfirmOpen(true)}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </Button>
        {isAdmin ? <AclDialog document={doc} open={aclOpen} onOpenChange={setAclOpen} /> : null}
        <MetadataDialog
          doc={doc}
          fields={fields}
          workspaceId={workspaceId}
          open={metadataOpen}
          onOpenChange={setMetadataOpen}
        />
        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent
            title="Delete document"
            description={`"${doc.filename}" and all its indexed chunks will be removed. This cannot be undone.`}
          >
            <DialogFooter>
              <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
              <Button
                variant="danger"
                disabled={deleting}
                onClick={() => {
                  onDelete();
                  setConfirmOpen(false);
                }}
              >
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </TD>
    </TR>
  );
}
