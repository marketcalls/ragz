import { ChevronDown, ChevronRight, SlidersHorizontal } from 'lucide-react';
import { Fragment, useState } from 'react';

import type { DocumentOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { useWorkspace } from '@/features/workspaces/workspace-context';
import { useWorkspaces } from '@/features/workspaces/queries';
import { WorkspaceSettingsDialog } from '@/features/workspaces/workspace-settings-dialog';

import { useClaims } from '@/lib/use-claims';

import { DocumentRow } from './document-row';
import { Dropzone } from './dropzone';
import { FolderCreateDialog, FolderDeleteDialog, FolderRenameDialog } from './folder-dialog';
import { buildFolderTree, useFolders } from './folder-queries';
import type { FolderNode } from './folder-queries';
import { FolderTree } from './folder-tree';
import { matchesMetadataFilter, MetadataFilterBar } from './metadata-filter-bar';
import { useDeleteDocument, useDocuments, useMetadataFields, usePinDocument } from './queries';
import { uploadDocuments } from './upload';
import { groupByLineage } from './versions';

interface UploadItem {
  key: string;
  names: string;
  pct: number;
}

export function DocumentsPage() {
  const { workspaceId } = useWorkspace();
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const folders = useFolders(workspaceId);
  const folderTree = buildFolderTree(folders.data ?? []);
  const documents = useDocuments(workspaceId, selectedFolderId);
  const deleteDocument = useDeleteDocument(workspaceId);
  const pinDocument = usePinDocument(workspaceId);
  const metadataFields = useMetadataFields(workspaceId);
  const fields = metadataFields.data ?? [];
  const [metadataFilter, setMetadataFilter] = useState<Record<string, string>>({});
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const claims = useClaims();
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';
  const { data: workspaces } = useWorkspaces();
  const workspace = workspaces?.find((w) => w.id === workspaceId) ?? null;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [creatingUnder, setCreatingUnder] = useState<string | null>(null);
  const [creatingOpen, setCreatingOpen] = useState(false);
  const [renamingFolder, setRenamingFolder] = useState<FolderNode | null>(null);
  const [deletingFolder, setDeletingFolder] = useState<FolderNode | null>(null);

  const groups = documents.data
    ? groupByLineage(documents.data).filter(({ current }) =>
        matchesMetadataFilter(current.meta, fields, metadataFilter),
      )
    : [];

  const onFiles = (files: File[]): void => {
    if (!workspaceId) return;
    const key = crypto.randomUUID();
    const names = files.map((f) => f.name).join(', ');
    setUploads((prev) => [...prev, { key, names, pct: 0 }]);
    uploadDocuments(workspaceId, files, (pct) =>
      setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, pct } : u))),
    )
      .then((failures) => {
        for (const failure of failures) toast.error(`${failure.file.name}: ${failure.message}`);
        void documents.refetch();
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setUploads((prev) => prev.filter((u) => u.key !== key)));
  };

  return (
    <>
      <TopBar
        title="Documents"
        actions={
          isAdmin && workspace ? (
            <Button
              variant="ghost"
              size="icon"
              aria-label="Workspace retrieval settings"
              onClick={() => setSettingsOpen(true)}
            >
              <SlidersHorizontal className="h-4 w-4" aria-hidden />
            </Button>
          ) : undefined
        }
      />
      {workspace && settingsOpen ? (
        <WorkspaceSettingsDialog workspace={workspace} open onOpenChange={setSettingsOpen} />
      ) : null}
      <FolderCreateDialog
        workspaceId={workspaceId}
        parentFolderId={creatingUnder}
        open={creatingOpen}
        onOpenChange={setCreatingOpen}
      />
      <FolderRenameDialog
        workspaceId={workspaceId}
        folder={renamingFolder}
        onOpenChange={(open) => !open && setRenamingFolder(null)}
      />
      <FolderDeleteDialog
        workspaceId={workspaceId}
        folder={deletingFolder}
        allFolders={folders.data ?? []}
        onOpenChange={(open) => !open && setDeletingFolder(null)}
      />
      <div className="flex flex-1 overflow-hidden">
        <div className="w-56 shrink-0 overflow-y-auto border-r border-line p-3">
          <FolderTree
            tree={folderTree}
            selectedId={selectedFolderId}
            onSelect={setSelectedFolderId}
            onNewChild={(parentId) => {
              setCreatingUnder(parentId);
              setCreatingOpen(true);
            }}
            onRename={setRenamingFolder}
            onDelete={setDeletingFolder}
          />
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mx-auto max-w-4xl space-y-4">
            <Dropzone onFiles={onFiles} disabled={!workspaceId} />
            {uploads.map((u) => (
              <div key={u.key} className="rounded-md border border-line bg-raised px-3 py-2">
                <div className="mb-1 flex justify-between text-[12px]">
                  <span className="truncate text-secondary">Uploading {u.names}</span>
                  <span className="tabular-nums text-muted">{u.pct}%</span>
                </div>
                <div className="h-1 overflow-hidden rounded-full bg-subtle">
                  <div className="h-full bg-accent transition-all" style={{ width: `${u.pct}%` }} />
                </div>
              </div>
            ))}
            {documents.isPending && workspaceId ? <Spinner label="Loading documents…" /> : null}
            {documents.data && documents.data.length > 0 && fields.length > 0 ? (
              <MetadataFilterBar
                fields={fields}
                value={metadataFilter}
                onChange={setMetadataFilter}
              />
            ) : null}
            {groups.length > 0 ? (
              <Table>
                <THead>
                  <TR>
                    <TH>Name</TH>
                    <TH>Size</TH>
                    <TH>Pages</TH>
                    <TH>Status</TH>
                    <TH>Uploaded</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {groups.map(({ current, older }) => {
                    const isExpanded = expanded.has(current.lineage_id);
                    const renderRow = (doc: DocumentOut, dimmed: boolean) => (
                      <DocumentRow
                        key={doc.id}
                        doc={doc}
                        workspaceId={workspaceId}
                        fields={fields}
                        dimmed={dimmed}
                        deleting={deleteDocument.isPending}
                        onDelete={() =>
                          deleteDocument.mutate(doc.id, {
                            onError: (err) => toast.error(err.message),
                          })
                        }
                        pinning={pinDocument.isPending}
                        onTogglePin={() =>
                          pinDocument.mutate(
                            { documentId: doc.id, pinned: !doc.pinned },
                            { onError: (err) => toast.error(err.message) },
                          )
                        }
                      />
                    );
                    return (
                      <Fragment key={current.lineage_id}>
                        {renderRow(current, false)}
                        {older.length > 0 ? (
                          <TR>
                            <TD colSpan={6} className="py-1.5">
                              <button
                                type="button"
                                className="flex items-center gap-1 text-[12px] text-secondary hover:text-ink"
                                onClick={() =>
                                  setExpanded((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(current.lineage_id)) {
                                      next.delete(current.lineage_id);
                                    } else {
                                      next.add(current.lineage_id);
                                    }
                                    return next;
                                  })
                                }
                              >
                                {isExpanded ? (
                                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                                ) : (
                                  <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                                )}
                                {older.length} older version{older.length === 1 ? '' : 's'}
                              </button>
                            </TD>
                          </TR>
                        ) : null}
                        {isExpanded ? older.map((doc) => renderRow(doc, true)) : null}
                      </Fragment>
                    );
                  })}
                </TBody>
              </Table>
            ) : null}
            {documents.data?.length === 0 && uploads.length === 0 ? (
              <p className="pt-4 text-center text-[13px] text-secondary">
                No documents yet — upload some to make them searchable.
              </p>
            ) : null}
            {documents.data && documents.data.length > 0 && groups.length === 0 ? (
              <p className="pt-4 text-center text-[13px] text-secondary">
                No documents match the current filters.
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
