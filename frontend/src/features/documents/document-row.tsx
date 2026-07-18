import { Lock, Pin, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { DocumentOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { StatusPill } from '@/components/ui/status-pill';
import { TD, TR } from '@/components/ui/table';

import { useClaims } from '@/lib/use-claims';

import { AclDialog } from './acl-dialog';
import { formatBytes, statusPresentation } from './status';

export function DocumentRow({
  doc,
  onDelete,
  deleting,
  onTogglePin,
  pinning,
}: {
  doc: DocumentOut;
  onDelete: () => void;
  deleting: boolean;
  onTogglePin: () => void;
  pinning: boolean;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [aclOpen, setAclOpen] = useState(false);
  const claims = useClaims();
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';
  const { tone, label } = statusPresentation(doc);
  const restricted = Boolean(doc.acl_group_ids && doc.acl_group_ids.length > 0);
  return (
    <TR>
      <TD className="max-w-[320px] truncate font-medium">{doc.filename}</TD>
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
        </div>
      </TD>
      <TD className="text-muted">{new Date(doc.created_at).toLocaleDateString()}</TD>
      <TD className="text-right">
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
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete ${doc.filename}`}
          onClick={() => setConfirmOpen(true)}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </Button>
        {isAdmin ? <AclDialog document={doc} open={aclOpen} onOpenChange={setAclOpen} /> : null}
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
