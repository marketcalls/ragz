import { useState, type FormEvent } from 'react';

import type { FolderDeletePreview, FolderOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/toaster';

import type { FolderNode } from './folder-queries';
import {
  useCreateFolder,
  useDeleteFolder,
  useFolderDeletePreview,
  usePatchFolder,
} from './folder-queries';

export function FolderCreateDialog({
  workspaceId,
  parentFolderId,
  open,
  onOpenChange,
}: {
  workspaceId: string | null;
  parentFolderId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateFolder(workspaceId);
  const [name, setName] = useState('');

  const submit = (e: FormEvent): void => {
    e.preventDefault();
    create.mutate(
      { name, parentFolderId },
      {
        onSuccess: () => {
          setName('');
          onOpenChange(false);
        },
        onError: (err: Error) => toast.error(err.message),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New folder">
        <form onSubmit={submit} className="space-y-3">
          <div>
            <Label htmlFor="folder-name">Name</Label>
            <Input
              id="folder-name"
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function FolderRenameDialog({
  workspaceId,
  folder,
  onOpenChange,
}: {
  workspaceId: string | null;
  folder: FolderNode | null;
  onOpenChange: (open: boolean) => void;
}) {
  const patch = usePatchFolder(workspaceId);
  const [name, setName] = useState(folder?.name ?? '');

  const submit = (e: FormEvent): void => {
    e.preventDefault();
    if (!folder) return;
    patch.mutate(
      { folderId: folder.id, name },
      {
        onSuccess: () => onOpenChange(false),
        onError: (err: Error) => toast.error(err.message),
      },
    );
  };

  return (
    <Dialog open={folder !== null} onOpenChange={onOpenChange}>
      <DialogContent title="Rename folder">
        <form onSubmit={submit} className="space-y-3">
          <div>
            <Label htmlFor="folder-rename">Name</Label>
            <Input
              id="folder-rename"
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={patch.isPending}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Counts every descendant folder (including itself) client-side from the
 * already-fetched FLAT folder list (FolderOut[], the raw useFolders(...).data
 * shape -- NOT FolderNode[]/the nested tree, which has no use for this
 * filter-by-parent_folder_id walk), so the confirmation message can name a
 * real count before the (irreversible) cascade delete -- the backend's
 * delete_folder only returns the DOCUMENT count, and only AFTER deleting,
 * so this pre-delete estimate is computed here, not fetched separately. */
function countSubtree(folderId: string, all: FolderOut[]): number {
  let count = 1;
  const children = all.filter((f) => f.parent_folder_id === folderId);
  for (const child of children) count += countSubtree(child.id, all);
  return count;
}

/** Builds the delete-confirmation text. Before the backend preview resolves
 * (or if it fails), falls back to the client-side subfolder-only estimate
 * with no document count -- a reasonable interim state rather than blocking
 * the dialog from opening. Once `preview` has loaded, both counts come from
 * the backend (the authoritative source for BOTH, not a mix of client/server
 * numbers), since delete_folder's actual cascade is what these must match. */
function buildDeleteDescription(
  folderName: string,
  clientSubfolderCount: number,
  preview: FolderDeletePreview | undefined,
): string {
  if (!preview) {
    const clause =
      clientSubfolderCount > 0
        ? ` and ${clientSubfolderCount} subfolder${clientSubfolderCount === 1 ? '' : 's'}`
        : '';
    return `"${folderName}"${clause} will be permanently deleted, along with every document inside. This cannot be undone.`;
  }
  const { document_count, subfolder_count } = preview;
  const subfolderClause =
    subfolder_count > 0 ? ` and ${subfolder_count} subfolder${subfolder_count === 1 ? '' : 's'}` : '';
  return `"${folderName}"${subfolderClause} will be permanently deleted, along with ${document_count} document${document_count === 1 ? '' : 's'} inside. This cannot be undone.`;
}

export function FolderDeleteDialog({
  workspaceId,
  folder,
  allFolders,
  onOpenChange,
}: {
  workspaceId: string | null;
  folder: FolderNode | null;
  allFolders: FolderOut[];
  onOpenChange: (open: boolean) => void;
}) {
  const del = useDeleteFolder(workspaceId);
  const preview = useFolderDeletePreview(folder?.id ?? null);
  const subfolderCount = folder ? countSubtree(folder.id, allFolders) - 1 : 0;

  return (
    <Dialog open={folder !== null} onOpenChange={onOpenChange}>
      <DialogContent
        title="Delete folder"
        description={
          folder ? buildDeleteDescription(folder.name, subfolderCount, preview.data) : undefined
        }
      >
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="danger"
            disabled={del.isPending}
            onClick={() => {
              if (!folder) return;
              del.mutate(folder.id, {
                onSuccess: (data) => {
                  toast(`Folder deleted — ${data.documents_deleted} document(s) removed`);
                  onOpenChange(false);
                },
                onError: (err: Error) => toast.error(err.message),
              });
            }}
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
